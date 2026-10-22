# Regression Witness

让 PR 审核者看到：**同一份回归测试，在修复前失败、修复后通过**，以及支持这个观察的命令、用例 ID、日志和结构化结果。

[English](README.md)

修复后的测试通过，还不足以说明它能抓住原来的问题。Regression Witness 将明确选定的 head 测试文件放入两个已提交版本的快照，执行同一命令，输出 Markdown 和 JSON，并将测试失败与测试缺失、跳过、执行错误及报告矛盾区分开。

面向本地可信仓库，为合并 bug fix 提供可复查的测试证据。

## 维护更新：v0.1.1

- JUnit 日志和 XML 注释中可以包含 `<!DOCTYPE ...>` 或 `<!ENTITY ...>` 字面文本，不再误拒绝合法报告。实际 DTD 声明仍不支持，由 XML 解析器拒绝。
- 报告声明未知 XML 编码时，CLI 现在按约定返回退出码 `2` 并说明错误，不再抛出未捕获的 traceback。
- 回归测试覆盖 UTF-8、UTF-16、内部与外部 DTD 声明及 CLI 错误处理路径；没有新增运行依赖。

## 先跑演示

需要 **Python 3.10+、Git 2.31+**。工具自身仅依赖 Python 标准库；目标平台是 macOS 和 Linux，Windows 尚未验证。

在本仓库根目录运行：

```sh
python3 -B examples/demo.py --output /tmp/witness-demo
```

输出目录必须尚不存在；再次运行时换一个新路径。

演示会创建一个明确标注为 synthetic 的示例仓库，提交一个字符串处理错误和修复，执行真实的 `unittest` 断言，再根据执行结果生成 JUnit XML。它不安装依赖，不访问网络，预期结果为 `FAIL_TO_PASS_OBSERVED`。

查看 `/tmp/witness-demo/evidence/report.md` 和 `report.json`。

## 在项目中运行

先准备项目自己的测试环境。工具不会安装项目、解决依赖或创建 virtual environment。在本仓库根目录运行：

```sh
python3 -m regression_witness run \
  --repo /path/to/project \
  --base BEFORE_FIX_COMMIT \
  --head AFTER_FIX_COMMIT \
  --overlay tests/test_bug.py \
  --test 'tests.test_bug::test_regression' \
  --command '/path/to/venv/bin/python -m pytest tests/test_bug.py --junitxml={junit}' \
  --output /tmp/witness-result \
  --repeats 2
```

将版本、路径、命令和用例 ID 替换为项目的真实值。输出目录必须尚不存在。

**`--test` 使用实际 JUnit XML 的 `classname::name`，不是 pytest node ID。** 例如：

```xml
<testcase classname="tests.test_bug" name="test_regression" />
```

对应 `tests.test_bug::test_regression`。实际 classname 受测试布局和 reporter 配置影响，请读取 XML，不要猜测转换规则。参数化用例名称保持原样。

- 多个测试或辅助文件分别用 `--overlay` 指定。工具从 head 取出这些已提交的普通文件，放入两侧快照。审核文件列表：放入生产代码会改变基线的含义。
- 多个目标用例分别用 `--test` 指定。每个选中的用例都必须满足失败到通过的条件。
- `--command` 必须包含字面量 `{junit}`，执行时会替换成该次运行的新报告路径。
- 命令解析成 argv 执行，不自动调用 shell；管道、重定向、环境变量赋值和 `~` 不会自动解释。
- CLI 的 `--repeats` 默认 `2`，范围 `1`–`10`；每次使用新快照。`--timeout` 默认每次命令 60 秒。

集成测试实际运行 pytest，覆盖失败到通过、collection error、skip 和 xfail/xpass。本地已使用 Python 3.14.3、pytest 9.1.1 完成验证。

## 实际比较的内容

基线是 **base 加上明确指定的 head 文件**，不是未经修改的 base；另一侧是 head 加上同一组文件。工具固定解析出的 commit SHA，读取 Git 原始 blob 建立临时快照，记录 overlay 文件的 SHA-256，并在执行后检查这些文件的内容是否变化。

这种方式不经过 checkout filters 或 `git archive` 的内容转换；`export-ignore`、`export-subst` 不会悄悄删除或改写测试文件。快照不含 `.git`。工具不改变原仓库的分支、index 或工作区文件，也不纳入未提交修改。

每次执行使用新的 JUnit 路径，并分别保留 stdout/stderr。报告记录版本、overlay hashes、用例 ID、命令参数、退出码、耗时、报告 hash 和逐次结果。重复执行的判定或目标用例变化不一致时，结果为 `INCONCLUSIVE`。

测试命令仍以你的权限运行，能够访问或修改快照以外的文件。文件快照**不是安全沙箱**。

## 判定与 CI 退出码

| 判定 | 含义 | CLI 退出码 |
| --- | --- | --- |
| `FAIL_TO_PASS_OBSERVED` | 所有选中用例均在前态失败、后态通过；报告无矛盾、无执行错误，后态没有其他失败。提供或捕获的退出码也必须一致。 | `0` |
| `NOT_REPRODUCED` | 所有选中用例在修复前已经通过，后态也通过。 | `1` |
| `STILL_FAILING` | 后态仍有失败，包括未选中用例的失败。 | `1` |
| `INCONCLUSIVE` | 现有证据不足以支持比较。 | `2` |

无效输入或准备错误也返回 `2`；部分错误发生在报告创建之前。`run` 必须获得实际退出码，并采用 pytest 的 `0`＝通过、`1`＝有测试失败语义。其他退出码、超时以及退出码与 XML 不符，均不构成成功证据。

目标用例缺失／跳过、任何执行错误、重复 ID、结果冲突、空报告和 suite 计数不符都不能得到肯定判定。选中的用例部分失败到通过、部分原本已通过，也不会整体判为成功。不支持的 retry/flaky 扩展结果同样阻止肯定结论。

输出结构：

```text
OUTPUT/
  report.md
  report.json
  runs/
    base-1/{junit.xml,stdout.log,stderr.log}
    head-1/{junit.xml,stdout.log,stderr.log}
    ...
```

执行或报告生成失败时，部分文件可能不存在。日志原样保存；分享前检查报告包中的内容。

## 比较已有报告

```sh
python3 -m regression_witness compare \
  --before /path/to/before.xml \
  --after /path/to/after.xml \
  --test 'tests.test_bug::test_regression' \
  --output /tmp/witness-comparison
```

可传入 `--before-exit`、`--after-exit` 检查调用者给出的 pytest 退出码是否与 XML 一致。**即使两个退出码都提供，此模式仍然只是 report-only。** 它不执行测试，无法验证 XML 来源、生成命令或是否使用同一测试。Hash 只标识输入字节，不证明真实性。

## 范围与限制

- 当前解析器面向常规 JUnit XML 和 pytest 退出码语义，支持 `testsuite`／`testsuites` 根与 XML namespace，不声称兼容所有 JUnit producer。拒绝 DTD／ENTITY 和超过 10 MiB 的报告。
- 两个完整 Git tree 都只能含普通文件：拒绝 symlink、submodule、不安全路径及 Git metadata 路径。单个快照上限 128 MiB／100,000 个文件，单个 overlay 文件上限 10 MiB。不处理 Git LFS 下载、submodule 初始化或依赖安装。
- 依赖 `.git` 的构建和测试可能无法运行。测试环境必须加载快照内的被测代码；指向原工作区的 editable install 可能破坏这一前提，工具尚不自动核验 import 来源。
- 依赖、环境变量、外部服务和凭证没有隔离，也没有完整记录。测试主体内的环境问题或错误断言仍可能表现为失败，请审核失败原因与 overlay 列表。
- 重复执行可暴露部分不稳定性，不证明不存在 flaky test；选定命令不替代项目完整测试套件。
- 测试代码与报告没有独立认证。报告是供审核的执行证据，不是可信第三方背书。

## 开发与贡献

在仓库根目录运行标准库测试：

```sh
python3 -B -m unittest discover -s tests -v
```

未安装 pytest 时，4 项 pytest 集成测试会明确跳过。使用已安装 pytest 的 Python 环境运行同一命令即可纳入这些测试；CLI 本身没有第三方运行依赖。

优先欢迎以下可核验的小型贡献：

1. 找一个已合并的真实 Python bug fix，固定 base/head 和 overlay，在已准备的 pytest 环境中复跑，记录真实用例 ID 与必要的环境配置。
2. 为可能误判成功的报告增加反例，例如某个 reporter 的 collection error 或 retry 结构。
3. 在 Linux、macOS 验证具体测试环境，记录明确的兼容边界。

在 [qjf1597300-cloud/regression-witness](https://github.com/qjf1597300-cloud/regression-witness) 提交 issue 或 PR，附最小 synthetic 示例或公开 commit 引用、预期／实际行为、Python／Git 版本与已脱敏报告。不要附凭证或私有源码。

## 邻近方案

失败到通过的验证已是成熟实践，本工具不声称独创这种方法：

- [Apache DataFusion 的 PR review 指南](https://datafusion.apache.org/contributor-guide/pr_review.html#review-the-test-coverage)要求撤销修复，确认新增测试会失败。
- [SWE-bench Harness](https://www.swebench.com/SWE-bench/api/harness/)已有 fail-to-pass、pass-to-pass 与基础设施故障分类，服务于带有任务及仓库环境配置的 benchmark。
- [GitHub Awesome Copilot quality-playbook](https://github.com/github/awesome-copilot/blob/main/skills/quality-playbook/SKILL.md)已规定 red/green 执行、JUnit、日志和结构化追踪。Regression Witness 聚焦将明确选择的测试对比做成固定行为的本地 CLI。
