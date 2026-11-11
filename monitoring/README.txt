每日增量监控与公司观点看板

本目录保存现有本地工作流的源码。投稿检查、观点归纳及外部背景查阅由本地
Codex 自动化按 DAILY_UPDATE.md 执行；GitHub 仓库本身不运行定时任务。
本地任务及其历史数据继续保留在 transcript_output/，不随源码上传。

文件
- ../bilibili_recent_batch.py：读取已核对的 TSV 目录，按作者、日期和时长校验并转写新视频。
- ../bilibili_repair_repeats.py：复核并重新识别连续重复的 ASR 片段，保留备份与修复记录。
- build_evidence.py：从识别片段提取候选证据；关键词命中不是观点归属。
- build_frequency.py：按当前公司及别名规则重算名称频率、涉及视频数及片段证据。
- render_report.py：读取已整理研究数据，输出带排序、频率证据和背景弹窗的离线看板。
- context_ui.js、context_ui.css：看板交互及样式，由渲染器内嵌。
- DAILY_UPDATE.md：每日取数、增量研究、校验与发布流程。

安装（MLX 转写需要 Apple Silicon；已有可用环境可直接复用）
python -m pip install -r requirements-monitoring.txt
cp config.example.json config.json
配置文件默认不读取浏览器登录态；需要访问受限视频时自行配置登录权限。

增量转写（从项目根目录执行，示例日期应按已核对目录调整）
python bilibili_recent_batch.py "transcript_output/任务/每日更新/2026-11-11/catalog.tsv" --uid 1962124785 --date-from 2026-11-08 --date-to 2026-11-11
TSV 必须包含 date、bvid、duration、title 列。截止日必须显式指定，不能依赖初始日期默认值。
失败项可用相同目录与日期范围加 --retry-failed 续跑；已有清单与目录不一致会拒绝运行。
增量完成后依流程按 BVID 合并主清单，再研究、渲染和发布。

重建现有看板（--task-dir 指向本地完整任务目录）
python monitoring/build_evidence.py --task-dir "transcript_output/任务"
python monitoring/build_frequency.py --task-dir "transcript_output/任务"
python monitoring/render_report.py --task-dir "transcript_output/任务" --final --output-dir "transcript_output/任务/AI公司观点整理_更新暂存"
先验证暂存结果，按 DAILY_UPDATE.md 备份和发布。--final 要求全部清单视频及研究已完成。

任务输入
- batch_manifest.json：主清单（含 items、date_from、date_to）。
- 研究证据/company_notes.json、labs_notes.json、supply_notes.json、extras_notes.json：公司观点。
- 研究证据/framework_notes.json：共同判断、时间线及口径。
- 研究证据/external_context.json：最新外部来源、背景卡、稳定的 公司ID:点序号 映射。
- 清单所引用的转写文本、SRT、segments_json，以及总转写目录。
这些研究数据由既有任务维护，不包含在源码仓库；新克隆不能直接重建历史看板。
现有清单含绝对文件路径；换机器时需保留数据并更新清单路径。
build_frequency.py 生成研究证据/frequency.json，供 render_report.py 使用。
初版 build_context.py 是会覆盖数据的旧研究种子，不作为每日更新入口；保留并增量维护最新背景数据。

验证
python -m unittest discover -s tests -v
测试使用离线样例或模拟依赖，不下载视频、不调用收费接口、不修改生产看板。
