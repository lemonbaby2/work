using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("SOP平台")]
[assembly: AssemblyDescription("宁波零部件 SOP 平台与 DGX 三路低延迟目标检测客户端")]
[assembly: AssemblyCompany("宁波零部件 SOP 项目")]
[assembly: AssemblyProduct("SOP平台")]
[assembly: AssemblyVersion("2026.8.21.0")]
[assembly: AssemblyFileVersion("2026.8.21.0")]

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
            Size = new Size(640, 450);
            MinimumSize = new Size(640, 450);
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
            Button monitorButton = new Button {
                Text = "打开 DGX 三路原生监控", Location = new Point(38, 294), Size = new Size(548, 40), FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(23, 56, 74), ForeColor = Color.White
            };
            startButton.Click += delegate { StartServer(); };
            stopButton.Click += delegate { StopServer(); };
            openButton.Click += delegate { OpenUrl(); };
            dataButton.Click += delegate { Process.Start("explorer.exe", appRoot); };
            monitorButton.Click += delegate { new NativeMonitorForm().Show(); };

            Label note = new Label { Text = "量产状态：HOLD。自动预标注、NG、稀有类别和工序边界须人工复核。", AutoSize = true, Location = new Point(39, 358), ForeColor = Color.FromArgb(150, 93, 25) };
            Controls.AddRange(new Control[] { title, subtitle, statusPanel, startButton, stopButton, openButton, dataButton, monitorButton, note });

            healthTimer = new System.Windows.Forms.Timer { Interval = 2000 };
            healthTimer.Tick += async delegate { await RefreshHealth(); };
            Shown += async delegate
            {
                healthTimer.Start();
                await RefreshHealth();
                if (!IsHealthy())
                {
                    if (File.Exists(Path.Combine(appRoot, "server.py"))) StartServer();
                    else SetState(false, "未配置本地后端；可直接打开 DGX 三路原生监控");
                }
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
                Path.Combine(appRoot, ".venv", "Scripts", "python.exe"),
                @"D:\Anaconda\envs\dl\python.exe",
                @"D:\Anaconda\python.exe",
                @"C:\ProgramData\miniconda3\envs\sop\python.exe",
                Path.Combine(appRoot, "python-runtime", "python.exe"),
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
            string detail = online ? "本机：http://127.0.0.1:" + Port + "  ·  局域网：http://" + FindLanAddress() + ":" + Port : (pythonPath == null ? "服务未启动" : "等待后端健康检查 · " + pythonPath);
            SetState(online, detail);
        }

        private string FindLanAddress()
        {
            try
            {
                foreach (IPAddress address in Dns.GetHostAddresses(Dns.GetHostName()))
                    if (address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(address))
                        return address.ToString();
            }
            catch { }
            return "本机IP";
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

    internal sealed class NativeMonitorForm : Form
    {
        private readonly TextBox serverBox;
        private readonly TextBox userBox;
        private readonly TextBox passwordBox;
        private readonly Label stateLabel;
        private readonly PictureBox[] feeds = new PictureBox[3];
        private readonly Label[] feedStates = new Label[3];
        private CookieContainer cookies;
        private CancellationTokenSource cancellation;
        private string serverUrl;

        internal NativeMonitorForm()
        {
            Text = "DGX 三路低延迟目标检测";
            Size = new Size(1180, 720);
            MinimumSize = new Size(920, 600);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(237, 242, 244);
            Font = new Font("Microsoft YaHei UI", 9F);

            Panel connection = new Panel { Dock = DockStyle.Top, Height = 86, BackColor = Color.White };
            connection.Controls.Add(new Label { Text = "DGX 地址", Location = new Point(18, 13), AutoSize = true });
            serverBox = new TextBox { Text = "http://192.168.1.129:8096", Location = new Point(18, 34), Width = 255 };
            connection.Controls.Add(serverBox);
            connection.Controls.Add(new Label { Text = "账号", Location = new Point(290, 13), AutoSize = true });
            userBox = new TextBox { Text = "", Location = new Point(290, 34), Width = 130 };
            connection.Controls.Add(userBox);
            connection.Controls.Add(new Label { Text = "密码", Location = new Point(437, 13), AutoSize = true });
            passwordBox = new TextBox { Location = new Point(437, 34), Width = 150, UseSystemPasswordChar = true };
            connection.Controls.Add(passwordBox);
            Button connectButton = new Button { Text = "连接并启动三路", Location = new Point(606, 30), Size = new Size(150, 34), BackColor = Color.FromArgb(13, 143, 121), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
            connectButton.Click += async delegate { await ConnectAll(); };
            connection.Controls.Add(connectButton);
            stateLabel = new Label { Text = "输入平台账号后连接；画面直接读取 DGX 最新检测帧。", Location = new Point(774, 35), Size = new Size(365, 32), ForeColor = Color.FromArgb(79, 103, 113) };
            connection.Controls.Add(stateLabel);
            Controls.Add(connection);

            TableLayoutPanel grid = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12), ColumnCount = 2, RowCount = 2 };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent, 50F));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent, 50F));
            for (int camera = 0; camera < feeds.Length; camera++)
            {
                Panel tile = new Panel { Dock = DockStyle.Fill, Margin = new Padding(6), BackColor = Color.FromArgb(7, 16, 24) };
                feeds[camera] = new PictureBox { Dock = DockStyle.Fill, SizeMode = PictureBoxSizeMode.Zoom, BackColor = Color.FromArgb(7, 16, 24) };
                feedStates[camera] = new Label { Text = "摄像头 " + camera + " · 待连接", Dock = DockStyle.Bottom, Height = 28, Padding = new Padding(8, 6, 0, 0), ForeColor = Color.White, BackColor = Color.FromArgb(23, 56, 74) };
                tile.Controls.Add(feeds[camera]);
                tile.Controls.Add(feedStates[camera]);
                grid.Controls.Add(tile, camera % 2, camera / 2);
            }
            Panel note = new Panel { Dock = DockStyle.Fill, Margin = new Padding(6), BackColor = Color.White };
            note.Controls.Add(new Label {
                Dock = DockStyle.Fill, Padding = new Padding(18),
                Text = "低延迟策略\r\n\r\n• 不加载完整管理网页，直接消费容量 1 的最新检测帧\r\n• 三路画面彼此独立重连\r\n• 关闭窗口不会停止 DGX 推理服务\r\n• 质量放行仍保持 HOLD，检测框必须人工复核",
                ForeColor = Color.FromArgb(45, 68, 78)
            });
            grid.Controls.Add(note, 1, 1);
            Controls.Add(grid);
            FormClosing += delegate { if (cancellation != null) cancellation.Cancel(); };
        }

        private async Task ConnectAll()
        {
            string rawUrl = serverBox.Text.Trim().TrimEnd('/');
            if (!Uri.IsWellFormedUriString(rawUrl, UriKind.Absolute) || userBox.Text.Trim().Length == 0 || passwordBox.Text.Length == 0)
            {
                stateLabel.Text = "请填写有效的 DGX 地址、账号和密码。";
                return;
            }
            stateLabel.Text = "正在登录并启动摄像头…";
            serverUrl = rawUrl;
            cookies = new CookieContainer();
            try
            {
                await Task.Run(() => Authenticate(userBox.Text.Trim(), passwordBox.Text));
                passwordBox.Clear();
                await Task.Run(() => Post("/api/camera/start?camera=all", "{}"));
                if (cancellation != null) cancellation.Cancel();
                cancellation = new CancellationTokenSource();
                for (int camera = 0; camera < feeds.Length; camera++)
                {
                    int cameraId = camera;
                    StartCameraStream(cameraId, cancellation.Token);
                }
                stateLabel.Text = "三路检测已连接；各路断开后自动重连。";
            }
            catch (Exception exc)
            {
                stateLabel.Text = "连接失败：" + exc.Message;
            }
        }

        private void StartCameraStream(int cameraId, CancellationToken token)
        {
            Task.Run(() => StreamCamera(cameraId, token));
        }

        private void Authenticate(string username, string password)
        {
            string json = "{\"username\":\"" + JsonEscape(username) + "\",\"password\":\"" + JsonEscape(password) + "\"}";
            Post("/api/auth/login", json);
        }

        private string Post(string path, string json)
        {
            byte[] body = System.Text.Encoding.UTF8.GetBytes(json);
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(serverUrl + path);
            request.Method = "POST";
            request.ContentType = "application/json";
            request.ContentLength = body.Length;
            request.CookieContainer = cookies;
            request.Timeout = 5000;
            using (Stream stream = request.GetRequestStream()) stream.Write(body, 0, body.Length);
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream())) return reader.ReadToEnd();
        }

        private void StreamCamera(int cameraId, CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    SetFeedState(cameraId, "摄像头 " + cameraId + " · 正在连接");
                    HttpWebRequest request = (HttpWebRequest)WebRequest.Create(serverUrl + "/api/camera/mjpeg?camera=" + cameraId);
                    request.CookieContainer = cookies;
                    request.Timeout = 7000;
                    request.ReadWriteTimeout = 7000;
                    request.KeepAlive = true;
                    using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    using (Stream stream = response.GetResponseStream()) ReadJpegFrames(cameraId, stream, token);
                }
                catch (Exception exc)
                {
                    if (token.IsCancellationRequested) return;
                    SetFeedState(cameraId, "摄像头 " + cameraId + " · 重连：" + exc.Message);
                    Thread.Sleep(1000);
                }
            }
        }

        private void ReadJpegFrames(int cameraId, Stream stream, CancellationToken token)
        {
            byte[] buffer = new byte[4 * 1024 * 1024];
            int count = 0;
            while (!token.IsCancellationRequested)
            {
                if (count == buffer.Length) count = 0;
                int read = stream.Read(buffer, count, buffer.Length - count);
                if (read <= 0) throw new IOException("检测流已断开");
                count += read;
                int start = FindMarker(buffer, count, 0xFF, 0xD8, 0);
                if (start < 0) { if (count > 65536) count = 0; continue; }
                int end = FindMarker(buffer, count, 0xFF, 0xD9, start + 2);
                if (end < 0) continue;
                int length = end + 2 - start;
                Bitmap frame;
                using (MemoryStream imageBytes = new MemoryStream(buffer, start, length, false, true))
                using (Image decoded = Image.FromStream(imageBytes)) frame = new Bitmap(decoded);
                ShowFrame(cameraId, frame);
                int remaining = count - end - 2;
                if (remaining > 0) Buffer.BlockCopy(buffer, end + 2, buffer, 0, remaining);
                count = remaining;
            }
        }

        private static int FindMarker(byte[] data, int length, byte first, byte second, int offset)
        {
            for (int index = Math.Max(0, offset); index < length - 1; index++)
                if (data[index] == first && data[index + 1] == second) return index;
            return -1;
        }

        private void ShowFrame(int cameraId, Bitmap frame)
        {
            if (IsDisposed) { frame.Dispose(); return; }
            BeginInvoke((Action)(() => {
                Image previous = feeds[cameraId].Image;
                feeds[cameraId].Image = frame;
                if (previous != null) previous.Dispose();
                feedStates[cameraId].Text = "摄像头 " + cameraId + " · 实时目标检测";
            }));
        }

        private void SetFeedState(int cameraId, string message)
        {
            if (!IsDisposed) BeginInvoke((Action)(() => feedStates[cameraId].Text = message));
        }

        private static string JsonEscape(string value)
        {
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n");
        }
    }
}
