# SOP 本地项目版本总表（2026-08-22）

## 当前与历史入口

访问 `http://192.168.1.129:8099` 查看统一入口。`8096` 已恢复为当前对话的完整集成版本，其他页面使用 `8101` 至 `8105` 并行保留。

详细端口、服务名和恢复方法见 `deploy/versioned/README.md`。源码快照范围见 `version-archive/README.md`。

## 已确认的本地资料来源

- 当前 Git 交付仓库：`/home/xjai/Desktop/sop xjai/work-delivery-repo`
- 2026-08-21 标注增强运行版：`/home/xjai/Desktop/sop xjai/SOP分析平台_摄像头接入与低延迟标注增强版_20260819/SOP分析平台_老板汇报版`
- 2026-08-22 视频库流程变体：`/home/xjai/sop_project/SOP分析平台_老板汇报版`
- 2026-08-20 协同标注清理版：`/home/xjai/Desktop/sop_xjai_clean_export5/.../SOP分析平台_老板汇报版`
- 2026-08-19 网页前后端版：`/home/xjai/Desktop/sop xjai/wangye qianhoudaun`
- 2026-08-19 第三摄像头版：`/home/xjai/Desktop/sop xjai/交付_20260819_第三摄像头/.../SOP分析平台_老板汇报版`
- 微信 SOP 最终说明文档：`/home/xjai/文档/xwechat_files/wxid_y7nnhh1o7ide22_8b48/msg/file/2026-08/`
- 官方 CVAT：`/home/xjai/tools/cvat`

`sop_xjai_clean_export2` 与 `sop_xjai_clean_export4` 的前端核心文件哈希一致，因此列为同一页面族，不重复占用端口。所有原始目录和大型交付 ZIP 仍保留在本机。

## GitHub 边界

GitHub 分支保存全部可公开复现的代码、APP/Windows 源码、配置模板、文档、测试报告和历史页面快照。生产数据库、登录密码、网盘访问参数、CVAT Token、摄像头凭据、视频和模型不进入公开 Git；它们在本机权限受控目录和既有离线交付包中保留。
