# Paper Graph · 论文引用图谱生成工具

> 输入一篇论文 → 递归检索引用与被引 → 生成交互式力导向图谱

基于 **OpenAlex API** 构建,完全免费、无需 API key。前端采用 **Cytoscape.js + fCoSE 布局**,资源本地化,**离线可用**(首次运行需联网检索论文,生成的 HTML 打开无需联网)。

---

## 快速开始

```bash
# arXiv ID (最常用)
python paper_graph.py --seed 1512.03385 --depth 2

# 标题搜索
python paper_graph.py --seed "Attention Is All You Need" --depth 2

# DOI
python paper_graph.py --seed 10.48550/arXiv.1512.03385 --depth 1

# 控制规模
python paper_graph.py --seed 1512.03385 --depth 2 --max-per-level 5 --max-total 50
```

运行后生成:
- `paper_graph.html` — 交互式图谱(浏览器直接打开,资源已本地化无需联网)
- `paper_graph.json` — 图谱原始数据(可用于二次开发)

> ⚠️ 生成的 HTML 引用同目录的 `vendor/`,**请勿移动 HTML 时脱离 vendor/ 目录**(否则需保持两者同级)。

---

## 参数说明

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--seed` | 必填 | 种子论文:arXiv ID / DOI / 标题 |
| `--depth` | 2 | 递归深度(0=只有种子,1=±直接关联,2=再深一层) |
| `--max-per-level` | 8 | 每个节点最多取多少 references / citations |
| `--max-total` | 80 | 图中最大节点总数(防止爆炸式增长) |
| `--output` | paper_graph.html | 输出 HTML 文件名 |

---

## 工作原理

```
种子论文
   │
   ├─ references (该论文引用了谁) ──→ 取 top N
   ├─ citations  (谁引用了该论文) ──→ 取 top N (按被引排序)
   │
   └─ 对每个结果递归执行上述操作 (BFS,受 depth/total 限制)
         │
         └─ 去重 + 构建有向图
               │
               └─ 输出 HTML (Cytoscape.js fCoSE 布局) + JSON
```

**数据源**: [OpenAlex](https://openalex.org) — 2.5 亿+ 学术成果的开放知识图谱,免费 API。脚本默认用一个占位邮箱进入 polite pool 获得更高速率,可在 `paper_graph.py` 顶部修改 `OA_MAILTO` 为你的邮箱。

---

## 可视化特性

### 布局: fCoSE
- 使用 **fCoSE**(复合力导向)布局,自动避免节点重叠
- 节点大小 ∝ log(被引数),引用量越大节点越大
- 种子节点星形 + 金色突出显示

### 着色: 绝对年份映射
- 节点颜色基于**距离 2026 年的绝对时间**,不随检索集合变化
- 越接近 2026 年越深,越久远越浅(早于 2000 年 clamp 到最浅)
- 同一年份的论文在任何检索结果中颜色一致

### 交互
- **点击节点** → 右侧详情面板滑入(标题/年份/来源/CCF-SCI 等级/作者/被引/引用数/摘要/arXiv 链接/DOI 链接/连接列表)
- **hover 节点** → 高亮整个邻域,其余节点淡化 + tooltip
- **顶部搜索框** → 实时高亮匹配节点并居中
- **适应屏幕** → 一键缩放到全部节点
- **物理引擎** → 重新触发布局
- **导出 JSON** → 下载图谱原始数据
- **ESC** → 关闭详情面板

### 期刊/会议等级
内置 70+ 条 CCF 推荐目录 + SCI 分区映射,详情面板显示彩色等级徽章(CCF-A/B/C、SCI-Q1/Q2、预印本等)。

---

## 示例文件

| 文件 | 说明 | 规模 |
|------|------|------|
| `example_resnet_d2.html` | ResNet depth=2 完整版 | 50 节点 / 57 边 |
| `example_resnet_d2.json` | 上述图谱原始数据 | — |
| `example_resnet_d1.html` | ResNet depth=1 精简版 | 9 节点 / 8 边 |
| `example_resnet_d1.json` | 上述图谱原始数据 | — |
| `example_resnet_light.html` | 浅色主题示例 | 11 节点 / 10 边 |
| `example_resnet_light.json` | 上述图谱原始数据 | — |

直接用浏览器打开 `.html` 文件即可查看交互式图谱(无需联网)。

---

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 数据获取 | OpenAlex API | 免费、无需 key、2.5 亿+ 学术成果 |
| 图谱构建 | Python BFS | 递归遍历 + 去重 + 规模控制(仅标准库) |
| 前端可视化 | Cytoscape.js 3.28 + fCoSE | 力导向布局,节点不重叠 |
| 样式 | Tailwind CSS | 运行时版本,已本地化至 `vendor/` |
| 字体 | 系统字体栈 | 无需加载 Google Fonts |

前端依赖(cytoscape / fCoSE / layout-base / cose-base / tailwind)已下载到 `vendor/` 目录并随仓库分发,生成的 HTML 通过相对路径引用,**完全离线可用**。

---

## 依赖

- **Python 3.8+**(仅用标准库:`urllib` / `json` / `argparse` / `math` / `collections`,无需 `pip install`)
- **现代浏览器**(Chrome / Edge / Firefox)

---

## 常见问题

**Q: 打开 HTML 一直显示"引擎初始化中"或空白?**
A: 检查 HTML 与 `vendor/` 目录是否同级(HTML 用相对路径 `vendor/` 引用资源)。强制刷新(Ctrl+F5)清缓存。请用 Chrome/Edge 打开。

**Q: 提示"找不到论文"?**
A: 检查种子论文 ID 是否正确。arXiv ID 格式为 `1512.03385`(不要加 `arXiv:` 前缀,脚本会自动处理)。标题搜索尽量用英文完整标题。

**Q: API 调用被限速?**
A: OpenAlex 免费池每秒 10 请求,脚本已内置 0.5s 间隔。如仍被限,稍后重试,或在 `paper_graph.py` 顶部将 `OA_MAILTO` 改为你的真实邮箱进入 polite pool。

**Q: 图谱节点太多太乱?**
A: 减小 `--depth`(用 1 而非 2)、减小 `--max-per-level`(用 5 而非 8)、减小 `--max-total`(用 30 而非 80)。

**Q: 节点有边引用了不存在的节点?**
A: 当 `--max-total` 截断节点时,部分边可能引用未入图的节点。脚本已自动过滤此类无效边(会在浏览器控制台提示丢弃数量),不影响渲染。

---

## 项目结构

```
paper-graph/
├── paper_graph.py          # 主程序(含 HTML 模板)
├── requirements.txt        # 依赖说明(仅标准库)
├── vendor/                 # 本地化的前端 JS 资源
│   ├── cytoscape.min.js
│   ├── cytoscape-fcose.js
│   ├── layout-base.js
│   ├── cose-base.js
│   └── tailwind.js
├── example_resnet_*.html   # 示例图谱
├── example_resnet_*.json   # 示例数据
├── README.md
└── LICENSE
```

---

## License

[MIT](LICENSE)
