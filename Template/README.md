Meta.yaml - 唯一的完整 Mihomo 部署模板。个人域名/IP 使用 MRS，个人其他条件使用 classical/list；第三方直引现成 MRS 或未经转换的文本，差量与转换补充采用 inline provider。保留原 42 条逻辑路由的匹配顺序、国内提前直连、OpenAI 美国策略、TelegramDC5、区域组、家庭代理组、FCM hosts、DNS 和 TUN 参数。

使用 [Shell/README.md](../Shell/README.md) 中的命令生成完整运行包；个人 MRS 仅在 GitHub Actions 编译。`config.yaml` 使用同包文件，`mihomo.yaml` 使用个人发布 URL、上游 URL 和内嵌补充。默认分支自动发布，功能分支手动设置 `publish_rules=true` 才发布。`rules` 分支只保留个人 `.mrs`/`.list`；完整配置从本模板或 Actions artifact 获取。

来源映射、classical 配套文件及逻辑路由/DNS 引用在 `Rule/build.yaml` 维护。修改后运行 `rules.py build --text-only --write-template` 更新完整模板，正式二进制仅在 Actions 编译；CI 使用 `--check-template` 防止分片引用过期。本地文本包仍使用真实文本格式。

`Kitony`、`Home_Proxy` 使用直连占位，使用前在本机替换订阅；这些占位不提供代理能力。控制器默认只监听环回地址，密钥留空。TUN 按新基准保持开启，BettBox 用户还需检查应用级覆写。原根目录配置含个人信息，不应提交或上传。

只有个人规则引用 `rules` 分支；第三方直接访问上游，必要补充在模板内嵌。GitHub 文件 URL 继续加 `https://gh-proxy.com/` 前缀，配置由 `Rule/build.yaml.github_proxy` 控制。`proxy: DIRECT` 连接反代站点，业务分流顺序不变。DNS 关键词和正则补充保留，运行配置不依赖 Geodata。跨客户端文件只随构建包提供，使用前查看 `clients/report.json` 的能力差异和策略名。

metaProvider.yml - 保留的旧 Mihomo 模板，当前脚本与 Actions 已切换到 `Meta.yaml`。

ClashProvider.yml - Clash Provider 手工配置模板。

forcerule.yml - Clash Subconverter 模板。

MixinTun - Clash 开启 TUN 的 mixin 配置模板。

QuanX.conf - Quantumult X 配置。

ShadowRocket.conf - Shadowrocket 配置。

SubconverterNginx.conf - Nginx 部分集成配置。

Surfboard.conf - 尚未完成的 Surfboard 模板；新的生成规则片段见构建包的 `clients/surfboard/`。
