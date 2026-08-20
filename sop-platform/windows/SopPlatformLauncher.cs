using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace SopPlatformLauncher
{
    internal static class Program
    {
        private static Mutex singleInstance;

        [STAThread]
        private static void Main()
        {
            bool created;
            singleInstance = new Mutex(true, "NingboSopPlatform.soplzp0820", out created);
            if (!created)
            {
                MessageBox.Show("SOP平台已经在运行。", "SOP平台", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
            singleInstance.ReleaseMutex();
        }
    }

    internal sealed class MainForm : Form
    {
        private const int Port = 8096;
        private readonly string appRoot;
        private readonly string runtimeRoot;
        private readonly Label stateLabel;
        private readonly Label detailLabel;
        private readonly Button startButton;
        private readonly Button stopButton;
        private readonly Button openButton;
        private readonly System.Windows.Forms.Timer healthTimer;
        private Process serverProcess;
        private StreamWriter logWriter;
        private string pythonPath;

        internal MainForm()
        {
            appRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            runtimeRoot = Path.Combine(appRoot, "runtime");
            Directory.CreateDirectory(runtimeRoot);

            Text = "宁波零部件 SOP 分析平台";
            Size = new Size(640, 390);
            MinimumSize = new Size(640, 390);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(237, 242, 244);
            Font = new Font("Microsoft YaHei UI", 9F);

            Label title = new Label { Text = "宁波零部件 SOP 分析平台", Font = new Font("Microsoft YaHei UI", 18F, FontStyle.Bold), AutoSize = true, Location = new Point(34, 30), ForeColor = Color.FromArgb(16, 34, 49) };
            Label subtitle = new Label { Text = "关键帧标注 · 三模型对比 · 实时摄像头 · SQLite 审计", AutoSize = true, Location = new Point(37, 72), ForeColor = Color.FromArgb(79, 103, 113) };

            Panel statusPanel = new Panel { Location = new Point(38, 110), Size = new Size(548, 105), BackColor = Color.White, BorderStyle = BorderStyle.FixedSingle };
            stateLabel = new Label { Text = "正在检查服务", Font = new Font("Microsoft YaHei UI", 13F, FontStyle.Bold), AutoSize = true, Location = new Point(20, 18), ForeColor = Color.FromArgb(13, 143, 121) };
            detailLabel = new Label { Text = "http://127.0.0.1:8096", AutoEllipsis = true, Location = new Point(21, 56), Size = new Size(500, 32), ForeColor = Color.FromArgb(101, 119, 131) };
            statusPanel.Controls.Add(stateLabel);
            statusPanel.Controls.Add(detailLabel);

            startButton = MakeButton("启动服务", 38, true);
            stopButton = MakeButton("停止服务", 174, false);
            openButton = MakeButton("打开网页", 310, true);
            Button dataButton = MakeButton("打开数据目录", 446, false);
            startButton.Click += delegate { StartServer(); };
            stopButton.Click += delegate { StopServer(); };
            openButton.Click += delegate { OpenUrl(); };
            dataButton.Click += delegate { Process.Start("explorer.exe", appRoot); };

            Label note = new Label { Text = "量产状态：HOLD。自动预标注、NG、稀有类别和工序边界须人工复核。", AutoSize = true, Location = new Point(39, 302), ForeColor = Color.FromArgb(150, 93, 25) };
            Controls.AddRange(new Control[] { title, subtitle, statusPanel, startButton, stopButton, openButton, dataButton, note });

            healthTimer = new System.Windows.Forms.Timer { Interval = 2000 };
            healthTimer.Tick += async delegate { await RefreshHealth(); };
            Shown += async delegate
            {
                healthTimer.Start();
                await RefreshHealth();
                if (!IsHealthy()) StartServer();
            };
            FormClosing += delegate { if (serverProcess != null && !serverProcess.HasExited) StopServer(); };
        }

        private Button MakeButton(string text, int x, bool primary)
        {
            return new Button {
                Text = text, Location = new Point(x, 240), Size = new Size(120, 40), FlatStyle = FlatStyle.Flat,
                BackColor = primary ? Color.FromArgb(13, 143, 121) : Color.White,
                ForeColor = primary ? Color.White : Color.FromArgb(23, 56, 74)
            };
        }

        private string FindPython()
        {
            string[] candidates = {
                Path.Combine(appRoot, "python-runtime", "python.exe"),
                Path.Combine(appRoot, ".venv", "Scripts", "python.exe"),
                @"D:\Anaconda\envs\dl\python.exe",
                @"D:\Anaconda\python.exe",
                @"C:\ProgramData\miniconda3\envs\sop\python.exe",
                @"C:\Python312\python.exe",
                @"C:\Python311\python.exe"
            };
            foreach (string candidate in candidates) if (File.Exists(candidate)) return candidate;
            string pathValue = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string folder in pathValue.Split(Path.PathSeparator))
            {
                try
                {
                    string candidate = Path.Combine(folder.Trim('"'), "python.exe");
                    if (File.Exists(candidate)) return candidate;
                }
                catch { }
            }
            return null;
        }

        private void StartServer()
        {
            if (serverProcess != null && !serverProcess.HasExited) return;
            string server = Path.Combine(appRoot, "server.py");
            if (!File.Exists(server))
            {
                MessageBox.Show("应用目录缺少 server.py。请保持 SOP平台.exe 与应用文件在同一目录。", "启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            pythonPath = FindPython();
            if (pythonPath == null)
            {
                MessageBox.Show("未找到 Python。请确认交付目录中包含 python-runtime，或安装 Python 3.11+。", "启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            string logPath = Path.Combine(runtimeRoot, "windows_launcher.log");
            logWriter = new StreamWriter(logPath, true) { AutoFlush = true };
            ProcessStartInfo info = new ProcessStartInfo {
                FileName = pythonPath,
                Arguments = "\"" + server + "\"",
                WorkingDirectory = appRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            info.EnvironmentVariables["SOP_HOST"] = "0.0.0.0";
            info.EnvironmentVariables["SOP_PORT"] = Port.ToString();
            serverProcess = new Process { StartInfo = info, EnableRaisingEvents = true };
            serverProcess.OutputDataReceived += LogLine;
            serverProcess.ErrorDataReceived += LogLine;
            serverProcess.Exited += delegate { BeginInvoke((Action)(() => SetState(false, "服务进程已退出；请查看 runtime\\windows_launcher.log"))); };
            serverProcess.Start();
            serverProcess.BeginOutputReadLine();
            serverProcess.BeginErrorReadLine();
            SetState(false, "正在启动 · " + pythonPath);
        }

        private void LogLine(object sender, DataReceivedEventArgs args)
        {
            if (args.Data != null && logWriter != null) lock (logWriter) logWriter.WriteLine(DateTime.Now.ToString("s") + " " + args.Data);
        }

        private void StopServer()
        {
            if (serverProcess != null && !serverProcess.HasExited)
            {
                try { serverProcess.Kill(); serverProcess.WaitForExit(3000); } catch { }
            }
            if (logWriter != null) { logWriter.Dispose(); logWriter = null; }
            SetState(false, "服务已停止");
        }

        private async Task RefreshHealth()
        {
            bool online = await Task.Run(() => IsHealthy());
            string detail = online ? "本机：http://127.0.0.1:" + Port + "  ·  局域网：本机IP:" + Port : (pythonPath == null ? "服务未启动" : "等待后端健康检查 · " + pythonPath);
            SetState(online, detail);
        }

        private bool IsHealthy()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + Port + "/api/health");
                request.Timeout = 900;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse()) return response.StatusCode == HttpStatusCode.OK;
            }
            catch { return false; }
        }

        private void SetState(bool online, string detail)
        {
            stateLabel.Text = online ? "服务在线" : "服务未就绪";
            stateLabel.ForeColor = online ? Color.FromArgb(13, 143, 121) : Color.FromArgb(201, 72, 66);
            detailLabel.Text = detail;
            startButton.Enabled = !online;
            stopButton.Enabled = online || (serverProcess != null && !serverProcess.HasExited);
            openButton.Enabled = online;
        }

        private void OpenUrl()
        {
            Process.Start(new ProcessStartInfo("http://127.0.0.1:" + Port) { UseShellExecute = true });
        }
    }
}
