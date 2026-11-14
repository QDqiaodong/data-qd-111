# data-qd-111 · 离线地址实体归并

Deterministic address linkage with conflict-aware connected components。数据流程与完整口径见 [docs/contract.md](docs/contract.md)。

## 安装

Python >=3.11，运行和测试仅使用标准库。先确认 `python3 --version`。
每个项目独立安装，不复用其他仓的虚拟环境或全局包：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps .
.venv/bin/python -m pip check
```

已具备相同版本的公共 wheel 缓存时，可用 `--no-index --find-links <wheel目录>` 安装构建工具；
否则首次安装可能联网。实际业务运行与测试无需网络、账号、Docker 或外部服务。

## 演示与测试

在仓库根目录执行；输出为可独立读取的 JSON，重跑全量重算、不累加。

```sh
.venv/bin/address-link --source fixtures/addresses.json --output run/report.json
.venv/bin/address-link --source fixtures/addresses.json --output run/audit.json --audit-only
.venv/bin/python -m unittest discover -s tests -v
```

## 历史维表（address-history）

`address-history` 把正常归并报告中的 `normalized_records` 导入 SQLite，按原样源 ID
维护左闭右开版本区间，并可导出任意已提交版本的快照。它不接受 `--audit-only` 诊断报告，
不落库 `group_id`，也不把分组当作持久身份。

```sh
.venv/bin/address-link --source fixtures/addresses.json --output run/report.json
.venv/bin/address-history import --database run/history.sqlite3 \
  --report run/report.json --version 1
.venv/bin/address-history as-of --database run/history.sqlite3 \
  --version 1 --output run/snapshot-v1.json
```

`import` 只以规范化后的 `city`/`postal`/`street`/`building`/`unit` 与原样 `registry_id`
判断六字段是否变化：未变不增行，变化关闭旧区间并以新版本新开，消失关闭，重现新开，
空批次关闭全部。新版本必须严格大于已提交最大版本；同版本再次导入时，六字段投影
（按 ID 排序）完全相同则幂等，不同则拒绝。版本登记与区间修改在同一事务内完成，
校验或写入失败不留下部分版本。`as-of` 对未知版本退出 2 并保留旧输出；导出记录按
原样 ID 升序，仅含 ID 与六字段。库 API：`address_link.history.import_report(...)`
与 `address_link.history.as_of(...)`。区间与完整口径见
[docs/contract.md](docs/contract.md)。

人工裁决为可选项，传入只含 `same`、`different` 的 JSON 文件（值为源ID二元组数组）：

```sh
.venv/bin/address-link --source fixtures/addresses.json \
  --decisions decisions.json --output run/report.json
```

```json
{"same": [["A", "B"]], "different": [["C", "E"]]}
```

same 先求传递闭包；different 若落入该闭包则整批拒绝（退出2、旧报告不变）。
候选边按 `(reason,left,right)` 排序逐条尝试，合并后会让任一 different 对同组的边被跳过，
审计中记录字典序最小的阻挡ID对。人工同组不豁免官方ID或地址断言冲突，分组仍按原口径标
review。报告新增 `decision_audit` 接纳/阻挡证据；不传 `--decisions` 时输出与旧契约一致。
完整口径见 [docs/contract.md](docs/contract.md)。

输入校验或计算失败退出 2，保留原输出；JSON 通过同目录临时文件完成后替换。
不对正在被其他进程写入的输出运行；不承诺文件系统断电恢复或在线并发发布。
修改源码后需重新安装。`run/`、`.venv/` 与构建产物均不入 Git。

## 导入前质量诊断（可选 --audit-only）

`--audit-only` 只逐行生成诊断报告，不建候选边、不分组，也不会改变默认
`address-link` 的输出。操作员可在正式导入前一次定位整批 `records` 的契约问题：
字段集、`id` 的空值/超长/非文本/重复、`registry_id`、四项地址必填项与 `unit`，
包括 NFKC 规范化后为空的地址/单元（如纯标点、软连字符）。每行收集全部独立问题。

```sh
.venv/bin/address-link --source fixtures/addresses.json --output run/audit.json --audit-only
```

报告为 `record_count`、`valid_count`、`problem_count`、`rule_counts` 与 `rows`。
`rows` 只含有问题的行，按源数组下标（0 起，不按 ID 排序）稳定排列，`index` 为下标、
`rule_codes` 为该行升序去重的规则码；重复 ID 的所有相关行都标 `id_duplicate`。
`rule_counts` 为各码覆盖的行数。示例：

```json
{"record_count": 3, "valid_count": 1, "problem_count": 2,
 "rule_counts": {"id_duplicate": 2, "postal_empty": 1},
 "rows": [{"index": 0, "rule_codes": ["id_duplicate"]},
          {"index": 1, "rule_codes": ["id_duplicate", "postal_empty"]}]}
```

JSON 语法/重复键、根结构错误和输入上限仍整批失败（退出 2、旧报告不变）；
报告同样原子替换。`--audit-only` 不能与 `--decisions` 同时使用。规则码与边界见
[docs/contract.md](docs/contract.md)。

样例来源见 fixtures/DATA-SOURCES.md。代码与自制样例 MIT；没有正式题面、评测答案或内部编排代码。

## 已有能力与样例结果

6条地址形成4组：A/B经Unicode规范化匹配；C/D因官方ID冲突进入review；未知单元的E/F保持独立。
`link.py`构建候选索引、并查集与裁决约束，`audit.py`提供导入前逐行诊断，`history.py`维护
SQLite 版本历史，`pipeline.py`衔接输入，`io.py`负责契约及原子报告，两个 CLI 供离线批处理使用。
当前没有模糊匹配；人工裁决支持 same/different 约束与阻挡审计，历史维表以原样源 ID 跨批次
保留六字段快照，后续可按真实业务演进。
