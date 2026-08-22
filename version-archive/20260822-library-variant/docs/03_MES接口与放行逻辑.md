# MES 接口与放行逻辑

## MES向平台下发

- 工单号、产品SN、车型/配置；
- 工位、班次和操作员；
- 应使用的SOP配方及版本；
- 可选的物料批次和特殊工艺参数。

## 平台向MES上传

- 每一步开始/结束时间、视觉结果和异常原因；
- 电动工具PSet、扭矩、角度、工具序列号和OK/NOK；
- 最终 PASS/HOLD/FAIL；
- 模型版本、SOP版本、关键帧/短视频地址；
- 唯一事件编号，用于防止重复上传。

## 建议结果示例

```json
{
  "event_id": "NB-IP-SOP-01-20260816-000001",
  "product_sn": "NB202608160001",
  "work_order": "WO20260816A",
  "station_id": "NB-IP-SOP-01",
  "recipe_id": "IP-ASSEMBLY-V1",
  "recipe_version": "2026.08.16",
  "model_version": "yolov8s-nb-ip-v1",
  "visual_sequence": "PASS",
  "torque_result": "PASS",
  "mes_ack": "OK",
  "final_result": "PASS",
  "evidence": ["frame_000812.jpg", "event_000001.mp4"]
}
```

## 最终放行规则

只有以下条件同时成立才允许 `PASS`：

1. 产品SN与工单匹配；
2. 必需零件全部检测到；
3. 所有SOP步骤按顺序完成；
4. 每个紧固点的PSet、扭矩、角度合格；
5. MES明确返回接收成功。

任何证据缺失均为 `HOLD`，由班组长或质量人员按权限处置，系统不得把“没有数据”当成“合格”。

## 联调前需要甲方提供

- MES测试地址、协议和鉴权方式；
- 工单下发与结果回传字段字典；
- 超时、重试、重复报文和撤销规则；
- 图片/视频由谁存储及保存期限；
- 生产放行是由MES、PLC还是工位系统最终执行。
