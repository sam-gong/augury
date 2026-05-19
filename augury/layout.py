"""Macro page layout: which charts appear where, in what order.

Each page has sections; each section has `featured` (full-width charts) and/or
`cards` (small value cards). Every entry has a 1-3 priority — the renderer
sorts by priority and the template shows a subtle tier indicator.

Each chart's `spec` field carries the exact data series IDs and transforms,
so a future implementer doesn't pick the wrong variant of a similarly-named
series. The renderer falls back to a placeholder if data is missing."""

from augury.strategies import SmaBand, SmaCross, HybridStrategy, ThermoBand
from augury.backtest import DEFAULT_START

PRICE_IDS = ["SPX", "NDX", "SPY", "QQQ", "VIX", "US10Y", "BTC",
             "NVDA", "TSLA", "AZO", "ORLY",
             "FICO", "META", "RKLB",
             "PLTR", "LLY", "NFLX", "APP", "AAPL"]

# ---------- Page definitions ----------

PAGES = {

    # ============================================================
    "cycle": {
        "label": "商业周期",
        "subtitle": "PMI 是同步指标;看它的领先指标预判未来 2-12 月方向。",
        "sections": [

            # ---- 长期关系
            {
                "key": "relation",
                "label": "长期关系",
                "subtitle": "PMI 是经济温度计,股市回报与 PMI 周期长期同步。",
                "featured": [
                    {
                        "kind": "regime", "priority": 1,
                        "title": "PMI 周期 vs 市场回报(长期对照)",
                        "desc": "课程「母图」:上图 ISM 综合 PMI 同比 (蓝) vs 标普 500 滚动年回报 (绿);下图标普/BTC 对数价格。经济好→公司挣钱→股市涨,看 PMI 方向看股市方向。",
                        "spec": "main: ISM_COMPOSITE_PMI (30% Mfg + 70% Svc 自计算) YoY diff;参考价: yfinance SPX, BTC-USD",
                        "source": "ISM · 自计算",
                    },
                    {
                        "kind": "line", "priority": 3,
                        "title": "Conference Board LEI 年增率",
                        "desc": "Conference Board 经济咨商局的领先指数,10 项指标合成。课程开篇用它「看到经济周期客观存在」,本框架仅作为同步参考(并非 PMI 的 5 个领先指标之一)。",
                        "spec": "(待接入) Conference Board LEI YoY — 不在 FRED 上,需 macromicro 或 Conference Board 付费数据",
                        "series_id": "CB_LEI",
                        "source": "Conference Board · 待接入",
                    },
                ],
            },

            # ---- 领先指标
            {
                "key": "leading",
                "label": "领先指标",
                "subtitle": "课程框架的 5 个 PMI 领先指标。把领先指标按其领先月数向右平移,与现 PMI 对齐;today 线右侧即「预测段」。",
                "featured": [
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "ISM 新订单 − 库存 → PMI (lead 2m)",
                        "desc": "新订单多、库存少 → 未来要补库 → PMI 上行;反之将走弱。课程提到的最直接领先指标,大致领先 2-3 月。",
                        "spec": "main: ISM_PMI · leader: ISM_NOC_MINUS_IVC (composite of DBnomics ISM/neword − ISM/inventories) · lead 2m",
                        "main_id": "ISM_PMI", "main_name": "ISM PMI",
                        "leader_id": "ISM_NOC_MINUS_IVC", "leader_name": "ISM 新订单 − 库存",
                        "lead_months": 2, "invert": False,
                        "source": "ISM via DBnomics",
                    },
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "全球央行降息比例 → PMI (lead 9m)",
                        "desc": "货币政策传导期约 9 月。降息比例越高 → 9 月后 PMI 越可能上行。",
                        "spec": "(待接入) 用 BIS WS_CBPOL 自算过几种口径(m/m 净降、12m 净降、低于 24m 高点),均无法稳定对齐 MM,差距 15-25pp 且在 2020/2009 峰值处方向甚至会反。MM 口径未公开,留白以避免误导。",
                        "main_id": "ISM_PMI", "main_name": "ISM PMI",
                        "leader_id": "GLOBAL_CB_CUT_RATIO", "leader_name": "全球央行降息比例",
                        "lead_months": 9, "invert": False,
                        "source": "MacroMicro · 待接入",
                    },
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "费城联储未来活动 6m → PMI (lead 6m)",
                        "desc": "直接问企业主对未来 6 个月的预期。课程评价:对底和顶都很清晰,是判断 PMI 拐点的高质量软指标。",
                        "spec": "main: ISM_PMI · leader: FRED:GAFDFSA066MSFRBPHI (Philly Fed Future Activity 6m) · lead 6m",
                        "main_id": "ISM_PMI", "main_name": "ISM PMI",
                        "leader_id": "GAFDFSA066MSFRBPHI", "leader_name": "费城联储未来活动 6m",
                        "lead_months": 6, "invert": False,
                        "source": "FRED:GAFDFSA066MSFRBPHI",
                    },
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "OECD 领先指标月变动扩散 → PMI (lead 6m)",
                        "desc": "OECD 综合领先指标中「上升」成员国占比。课程提醒:对顶部敏感,偶尔给假顶,需与其它领先指标交叉验证。",
                        "spec": "(待接入) leader: OECD 月度 CLI 中环比上升成员国占比 (扩散指数, 0-100) · main: ISM_PMI · lead 6m。OECD CLI 在 FRED 有 USALOLITONOSTSAM 但扩散需自算。",
                        "main_id": "ISM_PMI", "main_name": "ISM PMI",
                        "leader_id": "OECD_CLI_DIFFUSION", "leader_name": "OECD 扩散指数",
                        "lead_months": 6, "invert": False,
                        "source": "OECD · 待接入",
                    },
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "美联储金融脉冲增速指数 FCI-G → PMI (lead 3m, 反转视图)",
                        "desc": "Fed Board 的 FCI-G(1 年回看版,即「过去一年金融条件变化」对未来 GDP 增速的脉冲)。正值=金融条件收紧(逆风),负值=宽松(顺风)。反转后:线上行 → 经济顺风 → 未来 PMI 上行。",
                        "spec": "main: ISM_PMI · leader: FCI_G_1Y (Fed Board FCI-G, 1 年回看, monthly_1yr CSV — macromicro 同款) · 取负显示 · lead 3m",
                        "main_id": "ISM_PMI", "main_name": "ISM PMI",
                        "leader_id": "FCI_G_1Y", "leader_name": "FCI-G (反转)",
                        "lead_months": 3, "invert": True,
                        "source": "federalreserve.gov · FCI-G 1yr",
                    },
                    {
                        "kind": "overlay", "priority": 3,
                        "title": "10Y-2Y 收益曲线 → PMI (lead 12m+)",
                        "desc": "经典衰退信号:倒挂 (<0) → 12-18 月内衰退风险显著上升;转正回升 → 周期触底。课程未在 5 个领先指标内,但广为流通的标准衰退指标。",
                        "spec": "main: ISM_PMI · leader: FRED:T10Y2Y · lead 12m",
                        "main_id": "ISM_PMI", "main_name": "ISM PMI",
                        "leader_id": "T10Y2Y", "leader_name": "10Y-2Y Spread",
                        "lead_months": 12, "invert": False,
                        "source": "FRED:T10Y2Y",
                    },
                    {
                        "kind": "overlay", "priority": 3,
                        "title": "中国总信贷 YoY → PMI (lead 6-12m, 代理)",
                        "desc": "课程「母图」上的领先指标(未详谈)。**用 BIS「中国私营非金融部门总信贷」YoY 作为代理**,完整的「信贷脉冲」需用 PBoC 社融自算 (Δ流量/GDP),本图取季度数据作折中。",
                        "spec": "main: ISM_PMI · leader: FRED:CRDQCNAPABIS → YoY% (BIS 中国总信贷, 季度) · lead 9m。代理性指标,与 macromicro 严格 credit impulse 数值不完全等同。",
                        "main_id": "ISM_PMI", "main_name": "ISM PMI",
                        "leader_id": "CRDQCNAPABIS", "leader_name": "China Credit YoY (BIS 代理)",
                        "leader_transform": "yoy",
                        "lead_months": 9, "invert": False,
                        "source": "BIS via FRED:CRDQCNAPABIS",
                    },
                ],
            },

            # ---- 当前位置
            {
                "key": "position",
                "label": "当前位置",
                "subtitle": "此刻 PMI/NMI 与同步活动指标的读数。",
                "featured": [
                    {
                        "kind": "lines", "priority": 1,
                        "title": "ISM PMI / NMI",
                        "desc": "美国制造业 (PMI) 与服务业 (NMI),围绕 50 扩张/收缩分界波动。服务业占 GDP ~70%,两者同涨同跌决定经济温度。",
                        "spec": "ISM_PMI + ISM_NMI (jin10 历史 + investing.com 当期)",
                        "lines": [
                            {"series_id": "ISM_PMI", "label": "制造业 PMI", "color": "#3b82f6"},
                            {"series_id": "ISM_NMI", "label": "服务业 NMI", "color": "#f59e0b"},
                        ],
                        "y_label": "Index",
                        "source": "ISM",
                    },
                ],
                "cards": [
                    {"id": "GACDFSA066MSFRBPHI",  "priority": 2, "desc": "费城联储区域制造业「现况」指数(月),ISM 的高频补充。注意区分 GACD=Current、GAFD=Future。",
                     "spec": "FRED:GACDFSA066MSFRBPHI (Philly Fed Current Activity)"},
                    {"id": "CFNAI",               "priority": 3, "desc": "芝加哥联储全国活动指数(月),85 项月度活动合成,反映同步活动水平。",
                     "spec": "FRED:CFNAI"},
                    {"id": "INDPRO",              "priority": 3, "desc": "工业产出指数(月),制造+矿业+公用事业,同步活动量。",
                     "spec": "FRED:INDPRO"},
                ],
            },

        ],
    },

    # ============================================================
    "employment": {
        "label": "就业",
        "subtitle": "就业是经济周期的滞后结果;用 JOLTS 预判失业率和工资。",
        "sections": [

            # ---- 岗位
            {
                "key": "jobs",
                "label": "岗位",
                "subtitle": "有没有工作?看新岗位供给,失业率自然反应。",
                "featured": [
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "JOLTS 岗位空缺 → 失业率 (lead 3m, 左轴反转)",
                        "desc": "课程头牌:岗位空缺多 → 出门左转有工作 → 失业率下行。JOLTS 领先失业率约 3 月。本图把失业率轴反转,使两线同向上升才直观。",
                        "spec": "main: FRED:UNRATE (失业率, 左轴反转) · leader: FRED:JTSJOL (Job Openings Level, K) · lead 3m",
                        "main_id": "UNRATE", "main_name": "失业率",
                        "leader_id": "JTSJOL", "leader_name": "JOLTS 岗位空缺 (K)",
                        "lead_months": 3, "invert": False, "invert_main": True,
                        "source": "FRED:JTSJOL + UNRATE",
                    },
                ],
                "cards": [
                    {"id": "PAYEMS",              "priority": 2, "desc": "非农就业(大非农,K),每月初公布,月度就业总量。",
                     "spec": "FRED:PAYEMS (Total Nonfarm, Thousands)"},
                    {"id": "UNRATE",              "priority": 2, "desc": "失业率(%),Fed 主要关注;课程视为滞后指标。",
                     "spec": "FRED:UNRATE"},
                    {"id": "ICSA",                "priority": 3, "desc": "周初领失业金人数(每周四),高频边际信号。",
                     "spec": "FRED:ICSA (Initial Claims, SA)"},
                    {"id": "ADP_NFP_PRIVATE",     "priority": 3, "desc": "ADP 小非农(月),民间薪资数据,大非农的预演。",
                     "spec": "(待接入) ADP 月度全国就业报告 — ADP 官网发布,FRED 无稳定镜像"},
                    {"id": "CIVPART",             "priority": 3, "desc": "劳动参与率(%),结构性指标,揭示劳动力供给变化。",
                     "spec": "FRED:CIVPART"},
                    {"id": "CHALLENGER_JOBCUTS",  "priority": 3, "desc": "Challenger 裁员公告(月),边际景气恶化信号。",
                     "spec": "(待接入) Challenger, Gray & Christmas 月报 — 不在 FRED"},
                ],
            },

            # ---- 工资
            {
                "key": "wages",
                "label": "工资",
                "subtitle": "工资涨不涨?离职率领先工资增速 9 个月。",
                "featured": [
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "JOLTS 自主离职率 → Atlanta Fed 工资 (lead 9m)",
                        "desc": "离职率上升 9 月后,资本家被迫加薪(课程:「博弈九个月」)。判断未来工资压力、进而服务通胀最干净的领先关系。",
                        "spec": "main: ATLANTA_FED_WAGE_TRACKER (Atlanta Fed Wage Growth Tracker, 3mma Overall, 已为 YoY%, 抓 atlantafed.org Excel) · leader: FRED:JTSQUR (JOLTS Quits Rate, %) · lead 9m",
                        "main_id": "ATLANTA_FED_WAGE_TRACKER", "main_name": "Atlanta Fed 工资 YoY",
                        "leader_id": "JTSQUR", "leader_name": "JOLTS Quits Rate",
                        "lead_months": 9, "invert": False,
                        "source": "FRED:JTSQUR + ATLANTA_FED_WAGE_TRACKER",
                    },
                    {
                        "kind": "overlay", "priority": 2,
                        "title": "平均时薪 AHE: 水平 + 同比",
                        "desc": "生产/非主管私营雇员小时工资。左轴 = 水平($/小时, 平滑长期上行);右轴 = 同比 %(判断「工资涨没涨」的真实读数,raw 强调要看 YoY)。",
                        "spec": "FRED:CES0500000003 同一序列两条线:左轴原值 · 右轴 YoY%",
                        "main_id": "CES0500000003", "main_name": "AHE ($/小时)",
                        "leader_id": "CES0500000003", "leader_name": "AHE YoY %",
                        "leader_transform": "yoy",
                        "lead_months": 0, "invert": False,
                        "source": "FRED:CES0500000003",
                    },
                ],
                "cards": [
                    {"id": "ECI_WAGE",            "priority": 3, "desc": "雇佣成本指数 ECI 工资分项(季),工资+福利的总薪酬成本变化。",
                     "spec": "(待接入) FRED:ECIWAG 或 ECIALLCIV — 季度数据,需要单独 fetcher 处理季度频率"},
                ],
            },

        ],
    },

    # ============================================================
    "inflation": {
        "label": "通胀",
        "subtitle": "通胀是经济结果;Fed 通过利率调节需求侧,服务通胀最关键。",
        "sections": [

            # ---- 商品
            {
                "key": "goods",
                "label": "商品",
                "subtitle": "供给侧主导,Fed 难管,但传导路径清晰。",
                "featured": [
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "ISM 原物料价格 → CPI 同比 (lead 5m)",
                        "desc": "制造商进货价领先消费者物价约 5 月。关税战会激活这条传导路径,是判断商品通胀是否抬头的关键。",
                        "spec": "main: FRED:CPIAUCSL → 主线做 YoY 变换 (同比 %) · leader: ISM_MFG_PRICES (DBnomics ISM/prices/in, 原始值约 0-100) · lead 5m",
                        "main_id": "CPIAUCSL", "main_name": "CPI 同比 %",
                        "main_transform": "yoy",
                        "leader_id": "ISM_MFG_PRICES", "leader_name": "ISM 原物料价格",
                        "lead_months": 5, "invert": False,
                        "source": "ISM via DBnomics + FRED:CPIAUCSL",
                    },
                ],
                "cards": [
                    {"id": "PPIACO",  "priority": 2, "desc": "生产者物价指数 PPI,全部商品,上游价格信号。",
                     "spec": "FRED:PPIACO (PPI All Commodities)"},
                    {"id": "IR", "priority": 3, "desc": "进口物价指数(全部进口),关税和汇率冲击的直接体现。",
                     "spec": "FRED:IR (Import Price Index, All Imports)"},
                ],
            },

            # ---- 服务
            {
                "key": "services",
                "label": "服务",
                "subtitle": "Fed 真正关心的板块。房租是最大头,工资是单一驱动。",
                "featured": [
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "Atlanta Fed 工资 ↔ CPI Shelter 同比 (同期, 单因素)",
                        "desc": "房子供给僵硬,房租基本由需求侧(工资)单因素驱动。两条线几乎亦步亦趋,「需求侧通胀」的最干净视角。",
                        "spec": "main: FRED:CUSR0000SAH1 → YoY% (CPI Shelter 同比) · leader: ATLANTA_FED_WAGE_TRACKER (Atlanta Fed Wage Growth Tracker, 已为 YoY%) · lead 0",
                        "main_id": "CUSR0000SAH1", "main_name": "CPI Shelter 同比 %",
                        "main_transform": "yoy",
                        "leader_id": "ATLANTA_FED_WAGE_TRACKER", "leader_name": "Atlanta Fed 工资 YoY",
                        "lead_months": 0, "invert": False,
                        "source": "FRED:CUSR0000SAH1 + ATLANTA_FED_WAGE_TRACKER",
                    },
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "Case-Shiller 房价 → CPI Shelter (lead 17m)",
                        "desc": "房价领先房租约 17 月:建房周期 + 房东追求回报率。房价不涨,未来房租也涨不起来。课程图用 NSA 版本(非季调)。",
                        "spec": "main: FRED:CUSR0000SAH1 → YoY% · leader: FRED:CSUSHPINSA → YoY% (Case-Shiller US National HPI, NSA 与课程图一致) · lead 17m",
                        "main_id": "CUSR0000SAH1", "main_name": "CPI Shelter 同比 %",
                        "main_transform": "yoy",
                        "leader_id": "CSUSHPINSA", "leader_name": "Case-Shiller 同比 %",
                        "leader_transform": "yoy",
                        "lead_months": 17, "invert": False,
                        "source": "FRED:CSUSHPINSA + CUSR0000SAH1",
                    },
                    {
                        "kind": "overlay", "priority": 1,
                        "title": "新租户租金指数 → CPI Shelter (lead ~9m, 3Q) — ⚠️ 数据停更于 2025q1",
                        "desc": "CPI Shelter 是调研价(问房东期望),新租户指数是实际成交价。二者背离时新租户更可信,领先约 3 季度。**注意:BLS 自 2025 年 10 月政府关门起暂停发布,最新数据截至 2025q1(-2.2% YoY),后续季度文件 BLS 官网与 Wayback 均 404。等 BLS 恢复发布。**",
                        "spec": "main: FRED:CUSR0000SAH1 → YoY% · leader: BLS New Tenant Repeat Rent Index (停更于 2025q1, 抓 bls.gov/pir/ntr/) · lead 9m",
                        "main_id": "CUSR0000SAH1", "main_name": "CPI Shelter 同比 %",
                        "main_transform": "yoy",
                        "leader_id": "BLS_NEW_TENANT_RENT", "leader_name": "BLS 新租户租金 YoY",
                        "lead_months": 9, "invert": False,
                        "source": "BLS · 停更 2025q1",
                    },
                ],
                "cards": [
                    {"id": "PCEPILFE", "priority": 1, "desc": "核心 PCE(月),**Fed 2% 通胀目标的官方锚定指标**,剔除食品和能源。所有市场对 Fed 的预期都最终落到这。",
                     "spec": "FRED:PCEPILFE (Core PCE Price Index)"},
                    {"id": "CPILFESL", "priority": 1, "desc": "核心 CPI(月),市场最关注的月度通胀读数,每月发布日定盘当日波动。",
                     "spec": "FRED:CPILFESL (CPI Less Food and Energy)"},
                    {"id": "CPIMEDSL", "priority": 3, "desc": "CPI 医疗服务(月)。raw 提到服务通胀两大头是「房租 + 医保」,房租覆盖完整,医保作为补充。",
                     "spec": "FRED:CPIMEDSL (CPI Medical Care)"},
                ],
            },

            # ---- 预期
            {
                "key": "expectations",
                "label": "预期",
                "subtitle": "市场和消费者对未来通胀的看法,直接影响 Fed 决策。",
                "cards": [
                    {"id": "TRUEFLATION",      "priority": 1, "desc": "Trueflation 每日高频通胀替代指标,用电商/能源/房租等实时数据合成。课程作为「大账方向」的快速读数提及,反应比官方 CPI 早 1-2 月。",
                     "spec": "(待接入) Trueflation API — truflation.com,非政府数据,日频"},
                    {"id": "T5YIE",            "priority": 2, "desc": "TIPS 5 年隐含通胀预期(日,FRED 自计算),市场化定价。",
                     "spec": "FRED:T5YIE (5-Year Breakeven Inflation Rate)"},
                    {"id": "T10YIE",           "priority": 2, "desc": "TIPS 10 年隐含通胀预期(日)。",
                     "spec": "FRED:T10YIE (10-Year Breakeven Inflation Rate)"},
                    {"id": "MICH",             "priority": 3, "desc": "密西根大学 1 年通胀预期(月,消费者调查)。",
                     "spec": "FRED:MICH (UMich 1Y Inflation Expectation, Median)"},
                    {"id": "NYFED_INFL_EXP_1Y","priority": 3, "desc": "纽约联储 1 年通胀预期(月,消费者调查)。",
                     "spec": "NYFED_INFL_EXP_1Y — newyorkfed.org/microeconomics/sce Excel,Median 1Y"},
                ],
            },

        ],
    },

    # ============================================================
    "background": {
        "label": "背景",
        "subtitle": "利率与流动性 — 解释为什么领先指标在动。",
        "sections": [

            # ---- 利率
            {
                "key": "rates",
                "label": "利率",
                "subtitle": "联邦基金利率和国债收益率,货币政策的直接体现。",
                "cards": [
                    {"id": "FEDFUNDS", "priority": 1, "desc": "联邦基金利率(月,%),Fed 的核心政策工具。",
                     "spec": "FRED:FEDFUNDS"},
                    {"id": "DGS2",     "priority": 1, "desc": "2 年期国债收益率(日,%),对 Fed 决策最敏感。",
                     "spec": "FRED:DGS2"},
                    {"id": "DGS10",    "priority": 1, "desc": "10 年期国债收益率(日,%),经济和通胀预期的综合反映。",
                     "spec": "FRED:DGS10"},
                    {"id": "T10Y2Y",   "priority": 2, "desc": "10Y-2Y 期限利差(日,pp),倒挂常预警衰退。",
                     "spec": "FRED:T10Y2Y"},
                ],
            },

            # ---- 流动性
            {
                "key": "liquidity",
                "label": "流动性",
                "subtitle": "Fed 资产负债表、TGA、RRP 共同决定净流动性。",
                "cards": [
                    {"id": "M2SL",      "priority": 1, "desc": "M2 货币供应(月,$B),广义货币流通。",
                     "spec": "FRED:M2SL (Seasonally Adjusted)"},
                    {"id": "WALCL",     "priority": 1, "desc": "美联储总资产(周,$M),QE/QT 的直接刻度。",
                     "spec": "FRED:WALCL (Total Assets, Level)"},
                    {"id": "WTREGEN",   "priority": 2, "desc": "财政部一般账户 TGA(周,$M),钱在 TGA 就不在市场。",
                     "spec": "FRED:WTREGEN"},
                    {"id": "RRPONTSYD", "priority": 2, "desc": "隔夜逆回购 RRP(日,$B),货币基金停泊处,反向流动性信号。",
                     "spec": "FRED:RRPONTSYD"},
                ],
            },

        ],
    },

}


def page(key: str) -> dict:
    return PAGES[key]


def sidebar_pages() -> list[tuple[str, dict]]:
    """Iterable of (key, page) for the sidebar nav, in display order."""
    return list(PAGES.items())


# ============================================================
# Strategy pages
# ============================================================
# Four sibling pages — indices / stocks / learning / crypto — that share the
# same sidebar and the same per-asset card layout. Each page lists its assets
# as anchored sections (sidebar shows ticker links under the page heading).
#
# Adding an asset = one entry below. Adding a strategy family = one Strategy
# subclass; backtest + render stay the same.
#
# Each "section" IS an asset; `key` is the anchor (ticker) and `label` is the
# display name. The sidebar template iterates sections uniformly with macro,
# so no template changes are needed.

_PARQET = "https://assets.parqet.com/logos/symbol/{}?format=png"


def _asset(ticker, name, logo_symbol, strategy=None, strategies=None,
           logo=None, **extra):
    """Build a strategy-page asset entry. `logo_symbol` is the parqet slug
    (often equal to ticker, except for indices that use SPY/QQQ as proxy).
    Pass `logo=...` for assets that need a fully-custom logo URL (e.g. BTC,
    which parqet returns as a Grayscale ETF mark).

    Pass either `strategy=X` (single) or `strategies=[X, Y, ...]` (multi).
    Single is sugar for `strategies=[X]`. Multi-strategy assets render with
    a tab switcher; single strategy stays as a plain card."""
    if strategies is None:
        strategies = [strategy] if strategy is not None else []
    return {
        "key": ticker, "label": name,
        "ticker": ticker, "name": name,
        "logo": logo or _PARQET.format(logo_symbol),
        "strategies": strategies,
        **extra,
    }


STRATEGY_PAGES = {
    "indices": {
        "label": "指数",
        "subtitle": "225 日均线策略 + 市场宽度",
        "layout": "full",
        "extras": "breadth",  # render breadth (NDFI / S5FI) placeholder block
        "sections": [
            _asset("QQQ", "Nasdaq 100 (QQQ)", "QQQ", SmaBand(ma=225, threshold=0.01), breadth="NDFI"),
            _asset("SPY", "S&P 500 (SPY)",    "SPY", SmaBand(ma=225, threshold=0.01), breadth="S5FI"),
        ],
    },
    "stocks": {
        "label": "正股",
        "subtitle": "实仓标的",
        "layout": "full",
        "sections": [
            _asset("NVDA", "NVIDIA", "NVDA", SmaBand(ma=225, threshold=0.01)),
            _asset("TSLA", "Tesla", "TSLA", strategies=[
                SmaCross(fast=5, slow=30),
                HybridStrategy(base=SmaCross(fast=5, slow=30),
                               substitutes={"AZO": 0.5, "ORLY": 0.5}),
                ThermoBand(enter_up=(80, 22),
                           exit_down=(70, 22), exit_up=(50,)),
                ThermoBand(enter_up=(80,), exit_down=(70,)),
            ]),
        ],
    },
    "learning": {
        "label": "学习仓",
        "subtitle": "观察池 — 价格 + 常用均线 + 温度计,策略待定",
        "layout": "full",
        "sections": [
            _asset("FICO", "Fair Isaac", "FICO"),
            _asset("META", "Meta",       "META"),
            _asset("RKLB", "Rocket Lab", "RKLB"),
            _asset("PLTR", "Palantir",   "PLTR"),
            _asset("LLY",  "Eli Lilly",  "LLY"),
            _asset("NFLX", "Netflix",    "NFLX"),
            _asset("APP",  "AppLovin",   "APP"),
            _asset("AAPL", "Apple",      "AAPL"),
        ],
    },
    "crypto": {
        "label": "币",
        "subtitle": "120 日均线 + 温度计策略",
        "layout": "full",
        "sections": [
            _asset("BTC", "Bitcoin", "BTC", strategies=[
                SmaBand(ma=120, threshold=0.01),
                ThermoBand(enter_up=(50, 75), exit_down=(50, 70)),
            ],
                   logo="https://s2.coinmarketcap.com/static/img/coins/200x200/1.png",
                   backtest_start="2017-08-17",
                   display_start="2017-08-17"),
        ],
    },
}


def strategy_page(key: str) -> dict:
    return STRATEGY_PAGES[key]


def sidebar_strategy_pages() -> list[tuple[str, dict]]:
    """Same shape as `sidebar_pages()` so the shared sidebar template
    iterates uniformly across all strategy sub-pages."""
    return list(STRATEGY_PAGES.items())
