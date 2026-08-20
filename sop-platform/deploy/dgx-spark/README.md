# DGX Spark 部署边界

Spark 负责模型训练、自动预标注、验证测试、模型仓库和主要视觉推理。IPC 负责工业相机采集、扫码、串口、PLC/电批、SOP 状态机本地缓存，并在 Spark 网络不可用时使用 `yolo11n.pt` 降级推理。

当前机器已经检测为 NVIDIA GB10 / ARM64 DGX Spark。执行以下命令建立模型仓库；同一文件系统优先创建硬链接，不重复占用模型空间：

```bash
python3 scripts/sync_models_to_spark.py --apply
```

默认模型仓库为 `/home/xjai/sop-model-store`，可通过 `SOP_SPARK_MODEL_DIR` 修改。`registry.json` 包含文件大小和 SHA-256，可用于 IPC/DGX 版本校验。

RT-DETRv2、D-FINE 和 Anomalib 当前只有算法注册，没有项目权重。必须取得合规权重或使用人工真值训练后，才能把状态改成可部署。三条产线量产模型仍需各自冻结测试集验收。
