# paper-radar

> arXiv 每日个性化雷达：从你的点赞里学偏好，只推你方向的新论文，并说明为什么相关。

[![tests](https://github.com/xingaorpw/paper-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/xingaorpw/paper-radar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

---

## 它解决什么问题

arXiv 每天新增几百篇，关键词订阅已经烂大街——而且**关键词不区分语境**。

我搜 `retrieval`，它给我推「Bern 大气反演代码」和「分数陈绝缘体」，因为这两篇摘要里都有 retrieval。

真正有用的信号不是关键词本身，而是**你对哪些关键词有反应**。

`paper-radar` 让你给论文打 like / skip，然后从你的反馈里学一套关键词权重，下次排序时用上。

### 真实输出

抓完 25 篇、只打了 6 个分之后：

```
$ python -m paper_radar digest --top 5
paper-radar · 2026-11-06 · 共 25 篇，展示 5 篇
====================================================================
[1] 分数 10.96 | 2026-11-05 | cs.CL
    Multimodal-SEA: Benchmarking AI Agent Security under Multimodal Social Engineering Attacks
    理由：匹配关键词 agent / agents
    https://arxiv.org/abs/2611.08281

[2] 分数 10.20 | 2026-11-05 | cs.AI, cs.NE
    SwarmEvolve: Latent Agent Swarms for Evolutionary Discovery and Optimization
    理由：匹配关键词 agent / agents
    https://arxiv.org/abs/2611.08483

[3] 分数 8.84 | 2026-11-05 | cs.AI, cs.CL, cs.LG
    Scientist World: Grow a Research World for AI-Scientists
    理由：匹配关键词 environments / first
    https://arxiv.org/abs/2611.08478
```

我点赞的三篇都和 agent 有关，于是 `agent` 被排到最前；我跳过的三篇是物理，直接被压到后面。

---

## 快速开始

```bash
pip install -r requirements.txt

# 1. 抓一批论文
python -m paper_radar fetch --query "cat:cs.CL" --max 50

# 2. 看看推荐
python -m paper_radar digest

# 3. 打分（只要 arxiv id）
python -m paper_radar rate 2611.08478 like
python -m paper_radar rate 2611.08475 skip

# 4. 再看一次，顺序变了
python -m paper_radar digest
```

### 命令

| 命令 | 作用 |
|---|---|
| `fetch --query <q> --max N` | 抓取 arXiv 并入库（按提交时间倒序） |
| `digest --top N` | 按画像排序，输出推荐 + 理由 |
| `rate <id> like\|skip` | 打分，并显示画像里变化最大的关键词 |
| `stats` | 论文总数、已评分数量、画像 Top 10 |

数据库默认在 `~/.paper-radar/radar.db`，可用 `PAPER_RADAR_DB` 覆盖。

---

## 工作原理

```
arXiv Atom API  →  入库(SQLite)  →  画像(关键词权重)  →  排序  →  理由
                                        ↑
                                    你的 like / skip
```

### 画像怎么算

1. 文本 = 标题 + 摘要，转小写，按非字母数字切词，去掉停用词和长度 < 3 的词
2. 每次 like：`w[word] += 1 × 时间衰减`；每次 skip：`w[word] -= 1 × 时间衰减`
3. 时间衰减：60 天半衰期——三个月前的偏好不该和昨天的一样重
4. 排序分数：`sum(w[word] for word in paper) / sqrt(len(words))`

除以 `sqrt(词数)` 是为了防止长摘要自动占便宜。

### 冷启动

一篇都没评过时，按提交时间倒序——先看最新的，这是最合理的默认。

---

## 已知限制

诚实说明，这个项目的画像比 StarScout 粗糙得多：

- **纯关键词，没有语义**。它不知道「agent」和「智能体」是一回事，也不理解同义改写。
- **会学到噪声词**。上面输出里 `first`、`environments` 就是这样混进来的——它们在点赞论文里出现频率高，但其实不是领域词。
- **arXiv ID 的数字片段会被当词**（比如 `000`、`152`），因为切词规则只看字符类别。目前无害，但是脏。
- **不读全文**，只用标题和摘要。
- **没有引用图谱**。这是最想要的功能——「这篇引用了你点赞过的那篇」比关键词匹配准得多，但需要额外的引用数据源。
- 只支持 arXiv，不支持 bioRxiv / OpenReview。

## Roadmap

- [ ] 用引用关系做相关性（S2 / OpenAlex API）
- [ ] 中文分词 + 同义词归一
- [ ] 按 category 分组统计，避免一个领域淹没其他领域
- [ ] 导出 Markdown 日报

## License

[MIT](LICENSE) © 2026 xingaorpw
