# SOP 网页发布说明

公网展示链接（临时隧道）：

<https://6b62ad7472b661.lhr.life>

源码目录：

`/home/xjai/Desktop/sop xjai/wangye qianhoudaun`

前端入口为 `web/index.html`，后端入口为 `server.py`。当前公网实例使用 `SOP_PUBLIC_READONLY=1`，所有人可以查看网页、视频、图表和公开 GET 接口，但不会允许写入标注、训练/部署登记、MES 测试或摄像头启停。

本机完整运行：

```bash
cd "/home/xjai/Desktop/sop xjai/wangye qianhoudaun"
python3 server.py
```

本机实例默认地址：`http://127.0.0.1:8096`。第三摄像头 RTSP 地址、账号和工厂内网设备只应在本机/内网实例配置，不要把认证信息写进网页或提交到公开仓库。

公网隧道是临时地址；终端服务或 SSH 隧道停止后链接会失效。长期发布应使用有固定域名、访问控制和 HTTPS 证书的云服务器或反向代理。
