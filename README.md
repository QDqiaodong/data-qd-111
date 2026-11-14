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
.venv/bin/python -m unittest discover -s tests -v
```

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

样例来源见 fixtures/DATA-SOURCES.md。代码与自制样例 MIT；没有正式题面、评测答案或内部编排代码。

## 已有能力与样例结果

6条地址形成4组：A/B经Unicode规范化匹配；C/D因官方ID冲突进入review；未知单元的E/F保持独立。
`link.py`构建候选索引、并查集与裁决约束，`pipeline.py`衔接输入，`io.py`负责契约及原子报告，CLI供离线批处理使用。
当前没有模糊匹配或跨批次稳定实体ID；人工裁决支持 same/different 约束与阻挡审计，后续可按真实业务演进。
