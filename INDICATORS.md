# 指标清单 / Indicator inventory

> 截至 2026-05-17。本文档对应 [augury/layout.py](augury/layout.py) 和 [augury/indicators/catalog.py](augury/indicators/catalog.py),按页面 → 章节 → 角色组织。
>
> 来源标记:**FRED** = St. Louis Fed API · **DBnomics** = api.db.nomics.world 免费镜像 · **ISM PDF** = ISM 公开月报 + Wayback 历史回填 · **官方 XLSX** = 直接抓发布方的 Excel · **scraped** = HTML/JSON 反向工程 · **(待接入)** = 还没写 fetcher 或需付费

---

## 大盘 (index.html)

[render.py](augury/render.py) 的首页 summary,无独立指标定义。

---

## 商业周期 (cycle.html)

PMI 是同步指标;看它的领先指标预判未来 2-12 月方向。

### 长期关系

| 角色 | 指标 | ID | 来源 | 频率 | 状态 |
|---|---|---|---|---|---|
| 大图主线 | ISM 综合 PMI 同比 (30% Mfg + 70% Svc) | `ISM_COMPOSITE_PMI` | 自计算 (composite of `ISM_PMI` + `ISM_NMI`) | M | ✅ |
| 大图参考 | S&P 500 滚动年回报 + 对数价格 | `SPX` | yfinance `^GSPC` | D | ✅ |
| 大图参考 | BTC/USD 对数价格 | `BTC` | yfinance `BTC-USD` | D | ✅ |
| 单图 | Conference Board LEI 年增率 | `CB_LEI` | — | M | ❌ 待接入(不在 FRED,需付费) |

### 领先指标(5+ 张 overlay 大图)

领先期是**实测**的,不是标称的 —— 跑 `python -m augury leadlag` 重算(见
[augury/leadlag.py](augury/leadlag.py)),完整结果连同分年代明细渲染在顶栏「验证」
页([docs/leadlag.html](docs/leadlag.html))。**可用领先 = 实测领先 − 发布时滞**,
排序看的是这一列:OECD CLI 领先 PMI 6 个月但晚 2 个月才发布,真正能用的只有 4 个月。

| 主线 | 领先指标 | 标称 | **实测** | **可用** | corr | 判定 | leader ID | 来源 |
|---|---|---|---|---|---|---|---|---|
| `ISM_PMI` | OECD CLI 月变动扩散 | 6m | **6m** | 4m | **+0.57** | ✅ 最强,1969 起六个年代同号 | `OECD_CLI_DIFFUSION` | OECD SDMX,自算扩散 |
| `ISM_PMI` | 费城联储未来活动 6m | 6m | **11m** | 11m | +0.46 | ✅ 但 80 年代只有 +0.03 | `GAFDFSA066MSFRBPHI` | FRED |
| `ISM_PMI` | 10Y-2Y 收益曲线 | 12m | **11m** | 11m | +0.42 | ✅ | `T10Y2Y` | FRED |
| `ISM_PMI` | ISM 新订单 − 库存 | 2m | **6m** | 6m | +0.80 | ⚠️ 只有 2015 年起,n_eff=15,**未经跨年代检验** | `ISM_NOC_MINUS_IVC` (自算) | DBnomics ISM/neword + ISM/inventories |
| `ISM_PMI` | Fed FCI-G 1Y(反转视图) | 3m | **2m** | 1m | −0.48 | ⚠️ 90 年代变号(+0.13) | `FCI_G_1Y` | federalreserve.gov CSV |
| ~~`ISM_PMI`~~ | ~~中国总信贷 YoY (BIS 代理)~~ | 9m | **0m** | −5m | **+0.10** | ❌ **失效,图已从 cycle.html 删除**。扫描校正后 p=1.00,0~24 个月无一位置优于随机平移。指标仍在 catalog/leadlag 里 | `CRDQCNAPABIS` | FRED (BIS) |
| `ISM_PMI` | 全球央行降息比例 | 9m | — | — | — | ❌ MM 口径未公开,留白 | `GLOBAL_CB_CUT_RATIO` | — |

未在本页但实测通过的两个:**UMich 1年通胀预期**(`MICH`,领先 14m,−0.42)和
**芝加哥联储 NFCI**(领先 3m,−0.38)。

以及一条对整页的限定:同一套检验跑 `leader → 标普未来6个月回报`,**18 个指标 0 个判定「稳健」**
—— 唯一显著的是 ISM 新订单−库存(−0.60),但它只有 2015 年后一个年代;其余全部
p >0.05 或跨年代变号。这些指标预判的是经济,不是股价。

### 当前位置

| 角色 | 指标 | ID | 来源 | 频率 |
|---|---|---|---|---|
| 大图 | ISM 制造业 PMI | `ISM_PMI` | jin10 历史(attr_id 28)+ investing.com 当期 | M |
| 大图 | ISM 服务业 PMI (NMI) | `ISM_NMI` | jin10 历史(attr_id 29)+ investing.com 当期 | M |
| Card | 费城联储现况 | `GACDFSA066MSFRBPHI` | FRED | M |
| Card | 芝加哥联储 CFNAI | `CFNAI` | FRED | M |
| Card | 工业产出 INDPRO | `INDPRO` | FRED | M |

**ISM 子项数据源备注:** ISM 2017 年起诉 FRED 后下架了所有 NAPMxxx,免费来源高度受限。我们用 DBnomics 拿 5 年(2021-01+),最新 8 个月用 ISM 官网 PDF 解析,更早历史通过 Wayback 上的历史 PDF 一次性回填(见 [augury/indicators/backfill_ism.py](augury/indicators/backfill_ism.py))。

---

## 就业 (employment.html)

就业是经济周期的滞后结果;用 JOLTS 预判失业率和工资。

### 岗位

| 角色 | 指标 | ID | 领先 | 来源 | 状态 |
|---|---|---|---|---|---|
| 大图主线 | 失业率(左轴反转) | `UNRATE` | — | FRED | ✅ |
| 大图领先 | JOLTS 岗位空缺 | `JTSJOL` | 3m | FRED | ✅ |
| Card | 非农就业 | `PAYEMS` | — | FRED | ✅ |
| Card | 失业率 | `UNRATE` | — | FRED | ✅ |
| Card | 周初领失业金 | `ICSA` | — | FRED | ✅ |
| Card | ADP 小非农 | `ADP_NFP_PRIVATE` | — | — | ❌ 待接入 |
| Card | 劳动参与率 | `CIVPART` | — | FRED | ✅ |
| Card | Challenger 裁员 | `CHALLENGER_JOBCUTS` | — | — | ❌ 待接入 |

### 工资

| 角色 | 指标 | ID | 领先 | 来源 | 状态 |
|---|---|---|---|---|---|
| 大图主线 | Atlanta Fed Wage Tracker(3mma Overall, YoY%) | `ATLANTA_FED_WAGE_TRACKER` | — | atlantafed.org Excel(直抓) | ✅ |
| 大图领先 | JOLTS 自主离职率 | `JTSQUR` | 9m | FRED | ✅ |
| 大图(双轴) | 平均时薪 AHE 水平 + 同比 | `CES0500000003` | — | FRED | ✅ |
| Card | 雇佣成本指数 ECI 工资分项 | `ECI_WAGE` | — | — | ❌ 待接入(季频,需自写 fetcher) |

---

## 通胀 (inflation.html)

通胀是经济结果;Fed 通过利率调节需求侧,服务通胀最关键。

### CPI 组件层级速查

```
CPI All Items (CPIAUCSL, ~100%)
├── 食品 ~13%   ├── 能源 ~7%
├── 商品 ex 食品能源 ~20%       ← ISM 原物料价格 5m 领先
└── 服务 ~60%
     └── Housing (CUSR0000SAH)
          └── Shelter (CUSR0000SAH1, ~33% of CPI)  ← 我们专门看这个
               ├── OER ~25%   ├── 房租 ~7.5%   ├── 旅店 ~1%
     └── 医疗服务 (CPIMEDSL, ~7%)
```

raw 强调:Shelter 供给僵硬 → 通胀完全由需求侧(工资)驱动 → 这是「需求侧通胀」最干净的视角,也是 Fed 真正能管的部分。

### 商品

| 角色 | 指标 | ID | 领先 | 来源 | 状态 |
|---|---|---|---|---|---|
| 大图主线 | CPI 全部项 同比 | `CPIAUCSL` (→YoY%) | — | FRED | ✅ |
| 大图领先 | ISM 制造业原物料价格 | `ISM_MFG_PRICES` | 5m | DBnomics + ISM PDF | ✅ |
| Card | PPI 全部商品 | `PPIACO` | — | FRED | ✅ |
| Card | 进口物价指数 | `IR` | — | FRED | ✅ |

### 服务

| 角色 | 指标 | ID | 领先 | 来源 | 状态 |
|---|---|---|---|---|---|
| 大图主线 | CPI Shelter 同比 | `CUSR0000SAH1` (→YoY%) | — | FRED | ✅ |
| 大图领先 ① | Atlanta Fed 工资(同期) | `ATLANTA_FED_WAGE_TRACKER` | 0 | atlantafed.org | ✅ |
| 大图领先 ② | Case-Shiller 全美房价 同比 | `CSUSHPINSA` (→YoY%) | 17m | FRED (NSA 与课程一致) | ✅ |
| 大图领先 ③ | BLS 新租户租金指数 YoY | `BLS_NEW_TENANT_RENT` | 9m | bls.gov + Wayback | ⚠️ **停更于 2025q1**(政府关门后 BLS 暂停发布) |
| Card | 核心 PCE(Fed 2% 目标的锚) | `PCEPILFE` | — | FRED | ✅ |
| Card | 核心 CPI | `CPILFESL` | — | FRED | ✅ |
| Card | CPI 医疗服务 | `CPIMEDSL` | — | FRED | ✅ |

### 预期

| 角色 | 指标 | ID | 来源 | 状态 |
|---|---|---|---|---|
| Card | Trueflation 日频通胀 | `TRUEFLATION` | truflation.com API | ❌ 待接入(需付费 key) |
| Card | TIPS 5Y 隐含通胀 | `T5YIE` | FRED | ✅ |
| Card | TIPS 10Y 隐含通胀 | `T10YIE` | FRED | ✅ |
| Card | UMich 1Y 通胀预期 | `MICH` | FRED | ✅ |
| Card | NY Fed SCE 1Y 通胀预期(中位数) | `NYFED_INFL_EXP_1Y` | newyorkfed.org Excel(直抓) | ✅ |

---

## 背景 (background.html)

利率与流动性 — 解释为什么领先指标在动。

### 利率

| 角色 | 指标 | ID | 来源 |
|---|---|---|---|
| Card | 联邦基金利率 | `FEDFUNDS` | FRED |
| Card | 2 年国债 | `DGS2` | FRED |
| Card | 10 年国债 | `DGS10` | FRED |
| Card | 10Y-2Y 利差 | `T10Y2Y` | FRED |

### 流动性

| 角色 | 指标 | ID | 来源 |
|---|---|---|---|
| Card | M2 货币供应 | `M2SL` | FRED |
| Card | 美联储总资产 | `WALCL` | FRED |
| Card | 财政部 TGA | `WTREGEN` | FRED |
| Card | 隔夜逆回购 RRP | `RRPONTSYD` | FRED |

---

## 价格(顶部 summary 与图表参考)

| 指标 | ID | 来源 |
|---|---|---|
| S&P 500 | `SPX` | yfinance `^GSPC` |
| Nasdaq 100 | `NDX` | yfinance `^NDX` |
| VIX | `VIX` | yfinance `^VIX` |
| US 10Y 收益率 | `US10Y` | yfinance `^TNX` |
| BTC/USD | `BTC` | yfinance `BTC-USD` |

---

## 其他已注册但未在 layout 上展示的指标

| ID | 标题 | 用途 | 来源 |
|---|---|---|---|
| `NFCI` | 芝加哥联储 NFCI | 候选指标(尚未上 layout) | FRED |

---

## 当前状态汇总

- **总指标数:** ~50(已在 catalog.py 注册)
- **正常工作:** 41 个 ✅
- **待接入(无 fetcher 或需付费):** 5 个 ❌ — Trueflation / ADP / Challenger / ECI / CB LEI / Global CB Cut Ratio
- **停更:** 1 个 ⚠️ — BLS 新租户租金(2025q1 起)
- **数据新鲜度:** 大部分指标更新到 **2026-04**(月度)、**2026-05** 上旬(周/日频)

## 数据可信度备注

- **官方 API**(FRED, FCI-G CSV, OECD SDMX):零误差。
- **直抓 Excel**(Atlanta Fed Wage / NY Fed SCE):**已经与官网当期数字三方核对,零误差**。
- **scraped**(jin10, investing.com, ISM PDF):依赖 HTML/PDF 格式,易因上游改版而 break。jin10 用于 ISM PMI/NMI 历史,投资.com 用于当期 tail。
- **代理指标**(China credit impulse 用 BIS 总信贷代理):描述里已写明非严格等同。
