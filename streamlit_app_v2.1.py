import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="사용자 행동 탐색 대시보드", layout="wide")
st.title("사용자 행동 탐색 대시보드")
st.caption("주별 세션 퍼널 | 플랫폼 · 유입 경로별 | 2025.07 ~ 2026.06")

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블 events를 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # 이번 대시보드에 필요한 테이블과 컬럼
    "app_events": ["event_id", "event_datetime", "customer_id", "session_id",
                   "event_name", "platform", "traffic_source"],
}

@st.cache_resource
def get_db():                              # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events를 만듦 (처음 한 번만)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols).to_sql(name, con, index=False)
    # 5주차 제외 규칙: QA 계정 제외 · 중복 이벤트 제거
    con.execute("""
        CREATE TABLE events AS
        SELECT event_id, event_datetime, customer_id, session_id, event_name, platform, traffic_source
          FROM (SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY session_id, event_datetime, event_name
                                          ORDER BY event_id) AS dup
                  FROM app_events
                 WHERE customer_id NOT LIKE 'QA%')
         WHERE dup = 1
    """)
    # 세션별로 묶는 집계가 빨라지도록 session_id에 인덱스를 붙임 (없으면 첫 실행이 몇 분 걸릴 수 있음)
    con.execute("CREATE INDEX idx_events_session ON events(session_id)")
    return con

# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 events 테이블을 SQL로 집계
# ------------------------------------------------------------
@st.cache_data
def query(sql):                            # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db())

# 주 × 플랫폼 × 유입 경로별 세션 퍼널
SQL_WEEKLY_FUNNEL = """
WITH s AS (                                 -- 세션의 첫 이벤트로 플랫폼 · 유입 경로 · 주 정하기
    SELECT session_id, platform, traffic_source,
           DATE(MIN(event_datetime), 'weekday 0', '-6 days') AS 주시작일
      FROM events
     GROUP BY session_id
)
SELECT s.주시작일,
       s.platform       AS 플랫폼,
       s.traffic_source AS 유입경로,
       COUNT(DISTINCT e.session_id)                                                       AS 세션수,
       COUNT(DISTINCT CASE WHEN e.event_name = 'view_restaurant' THEN e.session_id END)   AS 조회세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'add_to_cart'     THEN e.session_id END)   AS 장바구니세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'begin_checkout'  THEN e.session_id END)   AS 결제시작세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'order_complete'  THEN e.session_id END)   AS 주문세션,
       SUM(CASE WHEN e.event_name = 'payment_fail' THEN 1 ELSE 0 END)                     AS 결제실패
  FROM events e
  JOIN s ON e.session_id = s.session_id
 WHERE s.주시작일 < '2026-06-29'            -- 이틀치뿐인 마지막 주 제외
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

@st.cache_data
def load_data():
    data = query(SQL_WEEKLY_FUNNEL)
    data["주시작일"] = pd.to_datetime(data["주시작일"]).dt.date
    return data

df = load_data()                           # 주 × 플랫폼 × 유입 경로별 세션 퍼널

# ------------------------------------------------------------
# 사이드바 필터
# ------------------------------------------------------------
st.sidebar.header("필터")

weeks = sorted(df["주시작일"].unique())
selected_weeks = st.sidebar.select_slider(
    "기간",
    options=weeks,
    value=(weeks[0], weeks[-1])
)

platforms = sorted(df["플랫폼"].unique())
selected_platforms = st.sidebar.multiselect(
    "플랫폼",
    options=platforms,
    default=platforms
)

traffic_sources = sorted(df["유입경로"].unique())
selected_traffic_sources = st.sidebar.multiselect(
    "유입 경로",
    options=traffic_sources,
    default=traffic_sources
)

# 평소 범위 기준 기간 select_slider 추가 (기본값: 처음 26주)
default_baseline_end_idx = min(25, len(weeks) - 1)
baseline_weeks = st.sidebar.select_slider(
    "평소 범위 기준 기간",
    options=weeks,
    value=(weeks[0], weeks[default_baseline_end_idx]),
    help="문제가 없던 기간을 고르세요. 이 기간의 주별 값 범위를 '평소 범위'로 씁니다."
)

# 집단 조건(플랫폼·유입경로)만 적용한 데이터 g
g = df[
    (df["플랫폼"].isin(selected_platforms)) &
    (df["유입경로"].isin(selected_traffic_sources))
]

# 기존 f는 g에 기간 조건을 더해 만듦
f = g[
    (g["주시작일"] >= selected_weeks[0]) &
    (g["주시작일"] <= selected_weeks[1])
]

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

# ------------------------------------------------------------
# 2. KPI 카드 및 규칙 기반 설명 문장
# ------------------------------------------------------------
def rate(a, b):
    return a / b * 100 if b else 0

t = f[["세션수", "결제시작세션", "주문세션"]].sum()
c1, c2, c3 = st.columns(3)
c1.metric("세션 수", f"{t['세션수']:,}개")
c2.metric("주문 전환율 (방문 → 주문)", f"{rate(t['주문세션'], t['세션수']):.1f}%")
c3.metric("결제 전환율 (결제 시작 → 주문)", f"{rate(t['주문세션'], t['결제시작세션']):.1f}%")

# 규칙 기반 설명 문장 (st.info)
f_weeks = sorted(f["주시작일"].unique())
if len(f_weeks) < 4:
    st.info("📦 주문 전환율: 비교하려면 선택 기간을 4주 이상 골라 주세요")
    st.info("💳 결제 전환율: 비교하려면 선택 기간을 4주 이상 골라 주세요")
else:
    last_4_weeks = f_weeks[-4:]
    f_last4 = f[f["주시작일"].isin(last_4_weeks)]
    t_last4 = f_last4[["세션수", "결제시작세션", "주문세션"]].sum()
    
    curr_order_rate = (t_last4["주문세션"] / t_last4["세션수"] * 100) if t_last4["세션수"] > 0 else 0
    curr_payment_rate = (t_last4["주문세션"] / t_last4["결제시작세션"] * 100) if t_last4["결제시작세션"] > 0 else 0
    
    g_base = g[(g["주시작일"] >= baseline_weeks[0]) & (g["주시작일"] <= baseline_weeks[1])]
    g_base_weekly = g_base.groupby("주시작일")[["세션수", "결제시작세션", "주문세션"]].sum().reset_index()
    
    order_rates = []
    payment_rates = []
    for _, row in g_base_weekly.iterrows():
        if row["세션수"] > 0:
            order_rates.append(row["주문세션"] / row["세션수"] * 100)
        if row["결제시작세션"] > 0:
            payment_rates.append(row["주문세션"] / row["결제시작세션"] * 100)
            
    start_str = str(last_4_weeks[0])
    
    # 주문 전환율 문장
    if len(order_rates) < 4:
        st.info(f"📦 주문 전환율 ({start_str} 주부터 4주): 기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요")
    else:
        min_o, max_o = min(order_rates), max(order_rates)
        if curr_order_rate < min_o:
            diff_o = min_o - curr_order_rate
            status_o = f"평소 범위({min_o:.1f}% ~ {max_o:.1f}%)보다 {diff_o:.1f}%p 낮습니다."
        elif curr_order_rate > max_o:
            diff_o = curr_order_rate - max_o
            status_o = f"평소 범위({min_o:.1f}% ~ {max_o:.1f}%)보다 {diff_o:.1f}%p 높습니다."
        else:
            status_o = f"평소 범위({min_o:.1f}% ~ {max_o:.1f}%) 이내입니다."
        st.info(f"📦 주문 전환율 ({start_str} 주부터 4주): 이번 값 {curr_order_rate:.1f}%는 {status_o}")
        
    # 결제 전환율 문장
    if len(payment_rates) < 4:
        st.info(f"💳 결제 전환율 ({start_str} 주부터 4주): 기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요")
    else:
        min_p, max_p = min(payment_rates), max(payment_rates)
        if curr_payment_rate < min_p:
            diff_p = min_p - curr_payment_rate
            status_p = f"평소 범위({min_p:.1f}% ~ {max_p:.1f}%)보다 {diff_p:.1f}%p 낮습니다."
        elif curr_payment_rate > max_p:
            diff_p = curr_payment_rate - max_p
            status_p = f"평소 범위({min_p:.1f}% ~ {max_p:.1f}%)보다 {diff_p:.1f}%p 높습니다."
        else:
            status_p = f"평소 범위({min_p:.1f}% ~ {max_p:.1f}%) 이내입니다."
        st.info(f"💳 결제 전환율 ({start_str} 주부터 4주): 이번 값 {curr_payment_rate:.1f}%는 {status_p}")

st.caption(f"📏 자동 계산 · 평소 범위 기준 기간 {baseline_weeks[0]} ~ {baseline_weeks[1]} (주별 값)")

st.divider()

# ------------------------------------------------------------
# 3. 차트
# ------------------------------------------------------------
left, right = st.columns(2)

# 3-1. 주별 결제 전환율 (플랫폼별)
wp = f.groupby(["주시작일", "플랫폼"])[["결제시작세션", "주문세션"]].sum().reset_index()
wp["결제전환율"] = (wp["주문세션"] / wp["결제시작세션"] * 100).round(1)
fig1 = px.line(wp, x="주시작일", y="결제전환율", color="플랫폼", markers=True,
               title="주별 결제 전환율 (플랫폼별)")
fig1.update_layout(yaxis_title="결제 전환율 (%)", xaxis_title="")
left.plotly_chart(fig1, width="stretch")

# 3-2. 세션 퍼널
steps = {"1.방문": "세션수", "2.식당조회": "조회세션", "3.장바구니": "장바구니세션",
         "4.결제시작": "결제시작세션", "5.주문완료": "주문세션"}
fun = pd.DataFrame({"단계": list(steps), "세션수": [f[c].sum() for c in steps.values()]})
fig2 = px.funnel(fun, x="세션수", y="단계", title="세션 퍼널")
right.plotly_chart(fig2, width="stretch")

# 3-3. 유입 경로별 주문 전환율
ws = f.groupby("유입경로")[["세션수", "주문세션"]].sum().reset_index()
ws["주문전환율"] = (ws["주문세션"] / ws["세션수"] * 100).round(1)
ws = ws.sort_values("주문전환율")
fig3 = px.bar(ws, x="주문전환율", y="유입경로", orientation="h", text_auto=".1f",
              hover_data=["세션수"], title="유입 경로별 주문 전환율")
fig3.update_xaxes(range=[0, ws["주문전환율"].max() * 1.2] if not ws.empty else [0, 100])
fig3.update_layout(xaxis_title="주문 전환율 (%)", yaxis_title="")
st.plotly_chart(fig3, width="stretch")

st.divider()

# ------------------------------------------------------------
# 4. 집단 비교 영역
# ------------------------------------------------------------
st.subheader("집단 비교")
group_col = st.radio("비교 기준", ["플랫폼", "유입경로"], horizontal=True)

# 집단별 5단계 퍼널 데이터 집계
group_agg = f.groupby(group_col)[["세션수", "조회세션", "장바구니세션", "결제시작세션", "주문세션"]].sum().reset_index()

# 세션 수가 500개 미만인 집단 확인
low_session_groups = group_agg[group_agg["세션수"] < 500][group_col].tolist()
if low_session_groups:
    st.caption(f"⚠️ 세션 수가 500개 미만인 집단({', '.join(low_session_groups)})이 있어 값이 크게 흔들릴 수 있습니다.")

# 누적 퍼널 데이터 변환 (melt)
df_funnel_list = []
for _, row in group_agg.iterrows():
    g_name = row[group_col]
    base_session = row["세션수"]
    step_vals = {
        "1.방문": row["세션수"],
        "2.식당조회": row["조회세션"],
        "3.장바구니": row["장바구니세션"],
        "4.결제시작": row["결제시작세션"],
        "5.주문완료": row["주문세션"]
    }
    for step_name, val in step_vals.items():
        cum_rate = (val / base_session * 100) if base_session else 0
        df_funnel_list.append({
            group_col: g_name,
            "단계": step_name,
            "누적전환율": round(cum_rate, 1),
            "세션수": val
        })
df_cum_funnel = pd.DataFrame(df_funnel_list)

# 단계 전환율(직전 단계 대비 %) 데이터 변환
df_step_list = []
for _, row in group_agg.iterrows():
    g_name = row[group_col]
    s_vis = row["세션수"]
    s_view = row["조회세션"]
    s_cart = row["장바구니세션"]
    s_pay = row["결제시작세션"]
    s_order = row["주문세션"]

    transitions = [
        ("방문 → 식당조회", s_view, s_vis),
        ("식당조회 → 장바구니", s_cart, s_view),
        ("장바구니 → 결제시작", s_pay, s_cart),
        ("결제시작 → 주문완료", s_order, s_pay)
    ]

    for step_name, curr_val, prev_val in transitions:
        rate_val = (curr_val / prev_val * 100) if prev_val else 0
        df_step_list.append({
            group_col: g_name,
            "단계전환": step_name,
            "단계전환율": round(rate_val, 1),
            "기준세션": prev_val
        })
df_step_rate = pd.DataFrame(df_step_list)

# 좌우 배치 레이아웃
col_left, col_right = st.columns(2)

with col_left:
    fig_group_funnel = px.funnel(
        df_cum_funnel,
        x="누적전환율",
        y="단계",
        color=group_col,
        title=f"집단별 누적 전환율 퍼널 ({group_col} 기준)",
        hover_data=["세션수"]
    )
    fig_group_funnel.update_layout(xaxis_title="누적 전환율 (%)", yaxis_title="")
    col_left.plotly_chart(fig_group_funnel, width="stretch")

with col_right:
    fig_group_bar = px.bar(
        df_step_rate,
        x="단계전환율",
        y="단계전환",
        color=group_col,
        barmode="group",
        orientation="h",
        text_auto=".1f",
        hover_data=["기준세션"],
        title=f"집단별 단계 전환율 ({group_col} 기준)"
    )
    fig_group_bar.update_layout(xaxis_title="단계 전환율 (%)", yaxis_title="")
    col_right.plotly_chart(fig_group_bar, width="stretch")

st.divider()

# ------------------------------------------------------------
# 5. 드릴다운: 언제부터, 무엇 때문일까
# ------------------------------------------------------------
st.subheader("드릴다운: 언제부터, 무엇 때문일까")

# f에 존재하는 해당 기준의 값 목록 추출
available_groups = sorted(f[group_col].unique())

dd_col1, dd_col2 = st.columns(2)
with dd_col1:
    selected_sub_group = st.selectbox("자세히 볼 집단", options=available_groups)
with dd_col2:
    selected_step_name = st.selectbox(
        "단계",
        options=[
            "방문 → 식당조회",
            "식당조회 → 장바구니",
            "장바구니 → 결제시작",
            "결제시작 → 주문"
        ]
    )

# 다른 축 설정 (플랫폼 <-> 유입경로)
other_col = "유입경로" if group_col == "플랫폼" else "플랫폼"

# 선택한 집단만 필터링
f_sub = f[f[group_col] == selected_sub_group]

# 단계별 현재/이전 컬럼 매핑
step_mapping = {
    "방문 → 식당조회": ("조회세션", "세션수"),
    "식당조회 → 장바구니": ("장바구니세션", "조회세션"),
    "장바구니 → 결제시작": ("결제시작세션", "장바구니세션"),
    "결제시작 → 주문": ("주문세션", "결제시작세션")
}
curr_col, prev_col = step_mapping[selected_step_name]

# 주별 × 다른 축별 집계
weekly_sub = f_sub.groupby(["주시작일", other_col])[[curr_col, prev_col]].sum().reset_index()

# 직전 단계 세션이 0인 경우 0%로 채우지 않고 NaN(빈 값) 처리
def calc_rate_safe(row):
    prev_val = row[prev_col]
    if prev_val == 0 or pd.isna(prev_val):
        return None
    return round(row[curr_col] / prev_val * 100, 1)

weekly_sub["단계전환율"] = weekly_sub.apply(calc_rate_safe, axis=1)

# 차트 ① (위): 다른 축별로 쪼개어 선 차트 표시
fig_drill1 = px.line(
    weekly_sub,
    x="주시작일",
    y="단계전환율",
    color=other_col,
    markers=True,
    title=f"① {selected_sub_group}의 주별 {selected_step_name} 전환율 — {other_col}별로 쪼개 보기"
)
fig_drill1.update_layout(yaxis_title="전환율 (%)", xaxis_title="")
st.plotly_chart(fig_drill1, width="stretch")
st.caption("모든 갈래가 같은 시점에 함께 움직였다면, 그 축은 원인이 아닙니다.")

# 차트 ② (아래): 같은 기간 결제실패 건수를 주별 막대 (선택 집단: 진한 빨간색, 그 외: 회색 합산)
f_fail = f.copy()
f_fail["그룹구분"] = f_fail[group_col].apply(lambda x: selected_sub_group if x == selected_sub_group else "그 외")

weekly_fail = f_fail.groupby(["주시작일", "그룹구분"])["결제실패"].sum().reset_index()

# 색상 지정 (선택 집단: 진한 빨간색, 그 외: 회색)
color_map = {selected_sub_group: "#C0392B", "그 외": "#BDC3C7"}

fig_drill2 = px.bar(
    weekly_fail,
    x="주시작일",
    y="결제실패",
    color="그룹구분",
    barmode="stack",
    color_discrete_map=color_map,
    title=f"② 주별 결제 실패 건수 ({selected_sub_group} vs 그 외 집단)"
)
fig_drill2.update_layout(yaxis_title="결제 실패 건수", xaxis_title="")
st.plotly_chart(fig_drill2, width="stretch")
st.caption("문제가 생긴 시점과 함께 움직였다면 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다.")