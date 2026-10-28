Meta.yaml - 当前 Mihomo 规则构建模板，基于根目录个人 `Meta.yaml` 脱敏生成。保留原路由顺序、国内提前直连、OpenAI 美国策略、TelegramDC5、台湾区域组、香港负载均衡、家庭代理组、FCM hosts、DNS 和 TUN 参数。

使用 [Shell/README.md](../Shell/README.md) 中的命令生成完整运行包；正式 MRS 通过 GitHub Actions「构建并同步代理规则」编译。生成的 `config.yaml` 使用同包文件，`mihomo.yaml` 引用独立 `rules` 分支。默认分支成功构建后自动同步公开产物，功能分支只验证和上传。

`Kitony`、`Home_Proxy` 使用直连占位，使用前在本机替换订阅；这些占位不提供代理能力。控制器默认只监听环回地址，密钥留空。TUN 按新基准保持开启，BettBox 用户还需检查应用级覆写。原根目录配置含个人信息，不应提交或上传。

模板中的 SpeedTest、OpenAI 补充和第二份媒体净化入口需首次默认分支发布后才能远程获取；构建包内置对应文件。关键词和正则保留 classical 补充，配置及运行包不再依赖 Geodata。跨客户端文件是规则片段，使用前查看 `clients/report.json` 的能力差异和策略名。

metaProvider.yml - 保留的旧 Mihomo 模板，当前脚本与 Actions 已切换到 `Meta.yaml`。

ClashProvider.yml - Clash Provider 手工配置模板。

forcerule.yml - Clash Subconverter 模板。

MixinTun - Clash 开启 TUN 的 mixin 配置模板。

QuanX.conf - Quantumult X 配置。

ShadowRocket.conf - Shadowrocket 配置。

SubconverterNginx.conf - Nginx 部分集成配置。

Surfboard.conf - 尚未完成的 Surfboard 模板；新的生成规则片段见构建包的 `clients/surfboard/`。
