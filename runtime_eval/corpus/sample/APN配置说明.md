# APN 配置说明

## 概述

APN（Access Point Name，接入点名称）用于标识分组数据网络（PDN），决定用户面
报文经由哪个网关接入外部网络。在 5G 核心网中，APN 对应 DNN（Data Network
Name）。一线操作员在开通新业务时通常需要新增并下发 APN 配置。

## 关键参数

- `apn-name`：APN 名称，长度 1–63 个字符，区分大小写，全局唯一。
- `apn-type`：取值 `ipv4` / `ipv6` / `ipv4v6`，默认 `ipv4`。
- `dns-primary`：主 DNS 地址，必填。
- `mtu`：最大传输单元，默认 1500，可配置范围 1280–1500。
- `idle-timeout`：会话空闲超时时间，单位秒，默认 3600。

## 配置步骤

1. 进入配置模式：`config terminal`。
2. 创建 APN：`apn create name <apn-name> type <apn-type>`。
3. 配置主 DNS：`apn <apn-name> dns primary <ip>`。
4. 如需修改 MTU：`apn <apn-name> mtu <value>`。
5. 提交并激活：`commit`，随后 `apn <apn-name> activate`。

## 常见故障

- **APN 激活失败，提示 DNS not configured**：说明主 DNS 未配置，执行第 3 步补齐
  `dns primary` 后重新 `activate`。
- **终端无法获取 IPv6 地址**：检查 `apn-type` 是否为 `ipv6` 或 `ipv4v6`，若为
  `ipv4` 需改类型后重下发。
- **大包业务丢包**：通常为 MTU 不匹配，将 `mtu` 调整为 1400 后观察。

## 相关文档

更详细的 DNN 与切片对应关系见《切片与DNN映射手册》第 3 章。
