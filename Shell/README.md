# Mihomo 规则自动化

正式 `.mrs` 构建由 `.github/workflows/build-rules.yml` 在 GitHub Actions 执行。本机用于审计、文本配置测试和验证下载的运行包，不需要在 BettBox 安装目录中修改内核。

## Actions 构建与下载

1. 将本次规则、模板、脚本和工作流改动提交到 GitHub。涉及这些文件的 push 和 pull request 自动触发；也可在 Actions →「构建 Mihomo 规则」→ Run workflow 手动触发。
2. 工作流使用 Python 3.11、PyYAML 6.0.3、Ruff 0.14.3 和 Mihomo v1.19.33。内核归档及所有上游快照均校验 SHA-256。
3. 工作流先审计，再编译每个 domain/ipcidr 分片，将 MRS 回读为文本比较匹配集合，执行 `mihomo -t` 和隔离内核的 provider 实际加载检查。随后使用同一缓存离线重建，比较两次构建清单。
4. 下载对应提交的 `mihomo-rules-<commit>` artifact，解压其中的 `mihomo-rules.tar.gz`，得到完整 `mihomo/` 目录。目录内的相对路径应一并保留。

可用 GitHub CLI 操作已经发布到仓库的工作流：

```bash
gh workflow run build-rules.yml
gh run list --workflow build-rules.yml
gh run download <运行编号> -n mihomo-rules-<提交SHA> -D ./artifacts
tar -xzf ./artifacts/mihomo-rules.tar.gz -C ./artifacts
```

工作流只读仓库，不自动提交、推送或发布 release。CI 使用不含真实订阅的仓库模板；下载运行包后再在本机填写订阅。

## 本机环境与审计

脚本支持 Python 3.9 及以上。使用项目虚拟环境安装依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r Shell/requirements-rules.txt
.venv/bin/ruff check Shell/rules.py
.venv/bin/ruff format --check Shell/rules.py
.venv/bin/python Shell/rules.py audit --report ./dist/audit.json
```

`audit` 不需要 Mihomo，也不调用 MRS 编译器。首次审计会下载锁定规则并缓存到 `.cache/rules`；`--offline` 要求相应缓存已存在。随仓库保存的 Sukka 快照直接读取并校验。

所有相对参数以调用时的工作目录为准，默认路径以脚本所在仓库为准，因此可以从其他目录调用脚本。可覆盖 `--config`、`--rules-dir`、`--manifest`、`--lock`、`--cache`、`--output`；含空格路径请加引号。传入自定义规则目录时，同时提供与其对应的构建清单和锁文件。

## 构建接口与输出

以下为 **Actions 内部执行接口**，本机不执行正式 MRS 构建：

```bash
python Shell/rules.py build --config Template/metaProvider.yml \
  --rules-dir Rule --output dist/mihomo --mihomo /path/to/mihomo
```

输出内容：

| 路径 | 用途 |
| --- | --- |
| `config.yaml` | 引用同包文件的运行配置 |
| `rules/local/` | 本地规则的 MRS、对应文本输入及 classical 残留 |
| `rules/source/` | 去重、处理兼容性后的完整 classical 文本 |
| `rules/upstream/` | 实际参与匹配及去重的锁定上游 |
| `GeoSite.dat`、`GeoIP.dat`、`ASN.mmdb` | 锁定的 Geo 数据 |
| `audit.json` | 删除、保留覆盖和明确排除项，含来源、行号、理由及覆盖依据 |
| `manifest.json` | 内核版本、输入和文件 SHA-256、分片规则数 |
| `upstreams.lock.json` | 本次运行包使用的完整上游锁定信息 |

工具先在临时目录完成构建和检查，全部成功后才替换输出目录。已有非空目录必须包含本工具的 `.rules-build` 标记，避免误覆盖其他文件。下载失败、哈希改变、未知规则、空引用或内核解析警告均使构建失败。

## 去重与格式约束

- `Rule/*.list` 保持多客户端共享。仅清理同文件中完全等价或被父域完整覆盖的项；不根据 Mihomo 的策略删除共享文件里的上游重复项。
- 构建时只删除被**更早、同策略、同参数**上游完整覆盖的本地规则；如果该列表也用于 DNS，还必须由相同 DNS 条件中的上游覆盖。后置上游、策略不同或不能证明等价的情况写入报告并保留。
- 不把 `example.com` 当成 `notexample.com` 的父域；CIDR 只比较同地址族的完整包含，不因部分重叠删除规则。解析与 `no-resolve` 参数必须一致。
- 按原始连续区段拆分规则，保持进程、关键词、域名和 IP 的相对顺序及 provider 在路由表的位置。DOMAIN-SUFFIX 写成 `+.域名`，IP 输入只包含 CIDR，`no-resolve` 保留在对应 RULE-SET 上。
- MRS 仅用于 domain/ipcidr；关键词、进程、正则等保留 classical。全量 classical 上游也保留，不用纯域名导出冒充完整 GEOSITE。
- `Speedtest.list` 中的 USER-AGENT 原样保留给其他客户端。Mihomo 构建按 `Rule/build.yaml` 的明确清单排除该条并报告。新增未知规则会失败，不会静默丢弃。
- 空分片不生成二进制或占位规则，对应生成配置引用也移除。`Torrent.list`、`tw.list` 目前只编译产物，不新增业务路由。
- `fakeipfillter.txt` 保留 DNS 通配模式，不按普通域名去重、标准化或编译 MRS。

`Rule/upstreams.lock.json` 使用提交固定地址及 SHA-256。Sukka 的发布端点会变化，`Rule/snapshots/` 保存当次原始发布文本并保留其来源、许可证头。更新上游时，应显式选择新提交或替换快照、同步 SHA-256，然后通过 Actions 重新审计和构建；不能仅修改哈希来绕过意外变更。锁定运行包关闭 Geo 自动更新，更新通过重新构建进行。

## 配置语法与运行包验证

下载的原始运行包可执行：

```bash
.venv/bin/python Shell/rules.py check --output ./artifacts/mihomo --mihomo /path/to/mihomo
/path/to/mihomo -t -d ./artifacts/mihomo -f ./artifacts/mihomo/config.yaml
```

`check` 会检查文件哈希、YAML 重复键、名称/路径唯一性、策略与 DNS 引用，再回读 MRS 和短时启动隔离内核。订阅使用无凭证夹具；验证不证明真实节点可用。直接 `mihomo -t` 只证明配置可解析，单独使用不能代替 provider 实际加载和回读检查。

先验证下载包，再在副本中填入真实订阅。修改后 `config.yaml` 与原构建清单的哈希不同是预期的，此时以 `mihomo -t` 和客户端日志检查本机配置；不要修改原始 manifest 伪造构建校验。真实订阅、节点及密钥不得提交到仓库。

本机开发时可生成纯文本测试包，不会调用 `convert-ruleset`：

```bash
.venv/bin/python Shell/rules.py build --text-only \
  --output ./dist/mihomo-text-test --mihomo /path/to/mihomo
```

`manifest.json` 中 `mode: text` 明确区别于 Actions 的 `mode: mrs`。文本测试不能替代 MRS 二进制验证。

## BettBox 副本验证与恢复

1. 保留原 `Meta`，记录原配置文件 SHA-256、当前模式及覆写状态。
2. 解压 Actions 运行包到持久本地目录。通过 BettBox 的配置导入/文件功能新增 `Meta-重构测试`，使用独立 profile 和规则路径，不覆盖 `Meta`。
3. 在测试副本中填入本机订阅和必要客户端参数。BettBox 的运行目录不一定是导入文件所在目录，应将规则放入客户端 HomeDir 内的独立子目录，并相应调整 provider 路径。
4. 检查 profile 的脚本覆写和应用级 DNS/TUN 覆写，确认最终生效配置；只对副本关闭会覆盖待测项的设置。
5. 切换到副本，检查全部 provider、策略组、DNS 和日志，分别验证直连、代理、区域策略、自定义规则、拦截及 fake-ip。没有实际业务流量的项目应标注为未验证。
6. 有加载或网络问题立即切回 `Meta`。测试结束默认也恢复 `Meta`，核对原文件哈希和状态；测试副本可保留。

BettBox 的 `BettboxCore` 是客户端控制服务，并非标准 `mihomo convert-ruleset` CLI。Actions 使用官方 Mihomo；本机使用客户端实际核心验证兼容性。
