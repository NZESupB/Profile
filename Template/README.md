ClashProvider.yml - Clash Provider 手搓配置模板

forcerule.yml - Clash Subconvertor模板,没啥软用

metaProvider.yml - Mihomo 规则构建输入模板，参考 MyClash 结构；使用说明见 [Shell/README.md](../Shell/README.md)。

正式 MRS 由 GitHub Actions「构建 Mihomo 规则」生成，下载运行包后在本机填写订阅。模板中的 SpeedTest 文本源由构建工具处理兼容性后生成，因此应使用产物中的 `config.yaml`。订阅 provider 默认留空，区域组无订阅时为 REJECT；添加订阅后在客户端选择实际节点。DNS 默认开启，TUN 默认关闭；BettBox 用户需同时检查脚本和应用级覆写。

MixinTun - Clash 开启 tun 的 mixin 配置模板

QuanX.conf - Quantumult X 的懒人配置

ShadowRocket.conf - ShadowRocket 的懒人配置

SubconverterNginx.conf - Nginx部分集成配置文件

Surfboard.conf - Surfboard 的懒人配置（未完成别用
