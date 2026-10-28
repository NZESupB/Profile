Meta.yaml - 唯一的完整 Mihomo MRS 部署模板，基于根目录个人配置脱敏生成。域名/IP provider 全部使用 MRS，关键词、正则和进程等条件使用 classical/text 补充，不再另设 MRS 示例。保留原 42 条逻辑路由的匹配顺序、国内提前直连、OpenAI 美国策略、TelegramDC5、台湾区域组、香港负载均衡、家庭代理组、FCM hosts、DNS 和 TUN 参数。

使用 [Shell/README.md](../Shell/README.md) 中的命令生成完整运行包；正式 MRS 通过 GitHub Actions「构建并同步代理规则」编译。生成的 `config.yaml` 使用同包文件，`mihomo.yaml` 引用独立 `rules` 分支。默认分支成功构建后自动同步公开产物，功能分支只验证和上传。

来源映射、classical 配套文件及逻辑路由/DNS 引用在 `Rule/build.yaml` 维护。修改后运行 `rules.py build --text-only --write-template` 更新完整模板，正式二进制仅在 Actions 编译；CI 使用 `--check-template` 防止分片引用过期。本地文本包仍使用真实文本格式。

`Kitony`、`Home_Proxy` 使用直连占位，使用前在本机替换订阅；这些占位不提供代理能力。控制器默认只监听环回地址，密钥留空。TUN 按新基准保持开启，BettBox 用户还需检查应用级覆写。原根目录配置含个人信息，不应提交或上传。

模板中的全部规则入口引用 `rules` 分支的 MRS/classical 分片，首次默认分支发布后才能远程获取；构建包内置对应文件。所有 GitHub 文件 URL 加 `https://gh-proxy.com/` 前缀，生成器和下载函数由 `Rule/build.yaml` 的 `github_proxy` 配置。`proxy: DIRECT` 直接连接反代站点，业务分流顺序不变。关键词和正则保留 classical 补充，配置及运行包不依赖 Geodata。跨客户端文件是规则片段，使用前查看 `clients/report.json` 的能力差异和策略名。

metaProvider.yml - 保留的旧 Mihomo 模板，当前脚本与 Actions 已切换到 `Meta.yaml`。

ClashProvider.yml - Clash Provider 手工配置模板。

forcerule.yml - Clash Subconverter 模板。

MixinTun - Clash 开启 TUN 的 mixin 配置模板。

QuanX.conf - Quantumult X 配置。

ShadowRocket.conf - Shadowrocket 配置。

SubconverterNginx.conf - Nginx 部分集成配置。

Surfboard.conf - 尚未完成的 Surfboard 模板；新的生成规则片段见构建包的 `clients/surfboard/`。
