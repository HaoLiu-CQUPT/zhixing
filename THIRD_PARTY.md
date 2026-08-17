# 第三方组件与校验信息

本仓库不重新分发以下安装包或可执行文件。校验值记录的是 2026-08-17 用于复现已验证测试环境的固定文件，不代表第三方软件安全、合规或适合生产。

## 企业微信 4.1.33.6009

- 来源：腾讯官方下载
- URL：<https://dldir1.qq.com/wework/work_weixin/WeCom_4.1.33.6009.exe>
- 文件大小：`516890176` 字节
- SHA-256：`065F56C41514451820C8F4A1037133321106B7026F00991492B2F7AA235414A5`
- Authenticode：验证为有效；签名主体 `Tencent Technology (Shenzhen) Company Limited`

企业微信仍受腾讯的软件许可和服务规则约束。本项目不附带该安装包。

## Enterprise-WeChat-GPTbot V1.0.08-beta.1

- 上游仓库：<https://github.com/luolin-ai/Enterprise-WeChat-GPTbot>
- Release：<https://github.com/luolin-ai/Enterprise-WeChat-GPTbot/releases/tag/V1.0.08-beta.1>
- ZIP：<https://github.com/luolin-ai/Enterprise-WeChat-GPTbot/releases/download/V1.0.08-beta.1/Enterprise-WeChat-GPTbot.zip>
- ZIP 大小：`314053094` 字节
- ZIP SHA-256：`DAF79014AEBE681E5AE6B8CB5D372D7B99FCDD2A268F7848C0D1D381BF43102B`
- `newqi24.exe` 大小：`129775372` 字节
- `newqi24.exe` SHA-256：`D96B74EDA95BB24B0640D42D30B2CFD3756E3D26A8B70780AB93DB62F13E38E1`
- Authenticode：`NotSigned`

截至 2026-08-17，上游 GitHub 仓库未声明可识别的开源许可证。因此本仓库不提交、镜像或再分发 `newqi24.exe`；`setup-runtime.ps1` 仅在使用者明确传入 `-AcceptRisk` 后，从上游 Release 下载并提取该文件。

上游 ZIP 同时包含数据库、历史消息 CSV、图片和日志。安装脚本不会解压这些内容，只提取固定路径下的 `newqi24.exe`。SHA-256 一致只能证明文件与测试样本相同，不能证明代码安全。

## Dify

本地适配层使用 Dify Cloud 的 HTTPS Chat API：<https://api.dify.ai/v1>。Dify 服务、知识库、模型和额度分别受 Dify 及模型供应商条款约束；本仓库不包含 Dify 服务端代码或凭证。
