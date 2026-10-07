import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="사용자 행동 탐색 대시보드", layout="wide")
st.title("사용자 행동 탐색 대시보드")
st.caption("주별 세션 퍼널 | 플랫폼 · 유입 경로별 | 2025.07 ~ 2026.06")

# ------------------------------------------------------------
# 세션 상태 초기화 (필터 초기화 버튼을 위한 번호 관리)
# ------------------------------------------------------------
if "filter_version" not in st.session_state:
    st.session_state.filter_version = 0

v = st.session_state.filter_version

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

AI_NOTES = {
    "전체": "전체 세션 수는 28,840개이며 주문 전환율은 14.5%, 가장 낮은 단계 전환율은 조회에서 장바구니 단계의 41.4%로 나타났다. 전체 결제 전환율은 80.5% 수준을 보였다. 상품 조회 후 장바구니로 이어지는 퍼널 단계에서 이탈이 가장 컸던 것으로 보인다.",
    "Android": "안드로이드 세션 수는 16,053개이고 주문 전환율은 14.3%, 가장 낮은 단계 전환율은 조회에서 장바구니 단계의 41.8%였다. 2026-04-20 주부터 2026-06-01 주까지 결제 전환율이 크게 낮아지는 현상이 이어졌다. 해당 기간 동안 안드로이드 플랫폼의 결제 환경이나 결제 프로세스에 특정 이슈가 발생했을 가능성이 있어 확인이 필요하다.",
    "iOS": "iOS 세션 수는 12,787개이며 주문 전환율은 14.8%, 결제 전환율은 85.5%로 안드로이드보다 높았다. 가장 낮은 단계 전환율은 조회에서 장바구니 단계의 40.8%로 나타났다. iOS 사용자의 결제 완료 성향이 안드로이드보다 상대적으로 더 높았던 것으로 보인다.",
    "organic": "오가닉 유입 세션 수는 22,489개로 전체의 다수를 차지하며 주문 전환율은 16.0%, 결제 전환율은 80.4%였다. 가장 낮은 단계 전환율은 조회에서 장바구니 단계의 41.4%였다. 자연 유입 사용자는 기본 서비스 관여도가 높아 안정적인 전환 성과를 보인 것으로 보인다.",
    "paid_ad": "유료 광고 유입 세션 수는 1,595개이고 주문 전환율은 26.3%, 결제 전환율은 90.3%로 다른 집단 대비 매우 높았다. 가장 낮은 단계 전환율은 조회에서 장바구니 단계의 53.7%였다. 광고를 통해 유입된 사용자의 구매 의도가 일반 유입보다 훨씬 뚜렷했던 것으로 보인다.",
    "push": "푸시 유입 세션 수는 4,635개이며 주문 전환율은 3.2%, 결제 전환율은 63.8%로 전체 대비 크게 낮았다. 가장 낮은 단계 전환율은 세션에서 조회 단계의 28.4%였다. 푸시 알림을 통해 진입한 사용자의 콘텐츠 관심도가 상대적으로 낮아 초기 유입 후 이탈이 많았던 것으로 보인다.",
    "referral": "레퍼럴 유입 세션 수는 121개로 500개 미만이며 주문 전환율과 결제 전환율 모두 0.0%였다. 가장 낮은 단계 전환율은 결제시작에서 주문 단계의 0.0%였다. 표본 집단 크기가 너무 작아 해당 수치만으로 채널의 성과를 단정하기는 어려우며 추가 데이터 확인이 필요하다."
}
AI_SOURCE = "🤖 Gemini가 미리 만든 해설 · 전체 기간 기준"

# ------------------------------------------------------------
# 사이드바 필터
# ------------------------------------------------------------
st.sidebar.header("필터")

weeks = sorted(df["주시작일"].unique())
selected_weeks = st.sidebar.select_slider(
    "기간",
    options=weeks,
    value=(weeks[0], weeks[-1]),
    key=f"slider_weeks_{v}"
)

platforms = sorted(df["플랫폼"].unique())
selected_platforms = st.sidebar.multiselect(
    "플랫폼",
    options=platforms,
    default=platforms,
    key=f"multi_platforms_{v}"
)

traffic_sources = sorted(df["유입경로"].unique())
selected_traffic_sources = st.sidebar.multiselect(
    "유입 경로",
    options=traffic_sources,
    default=traffic_sources,
    key=f"multi_traffic_{v}"
)

# 평소 범위 기준 기간 select_slider를 고급 설정 expander 안으로 이동
with st.sidebar.expander("고급 설정"):
    default_baseline_end_idx = min(25, len(weeks) - 1)
    baseline_weeks = st.select_slider(
        "평소 범위 기준 기간",
        options=weeks,
        value=(weeks[0], weeks[default_baseline_end_idx]),
        help="문제가 없던 기간을 고르세요. 이 기간의 주별 값 범위를 '평소 범위'로 씁니다.",
        key=f"slider_baseline_{v}"
    )
    exclude_low_session = st.checkbox(
        "세션 500개 미만 집단 제외 (집단 비교 · 드릴다운)",
        value=False,
        key=f"chk_exclude_{v}"
    )

# 사이드바에 필터 초기화 버튼 추가
if st.sidebar.button("필터 초기화"):
    st.session_state.filter_version += 1
    st.rerun()

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

# ------------------------------------------------------------
# 제목 아래 조건 표시 st.caption 추가
# ------------------------------------------------------------
# 기간 표현 결정
if len(selected_weeks) == 2:
    if selected_weeks[0] == weeks[0] and selected_weeks[1] == weeks[-1]:
        period_str = "전체"
    else:
        period_str = f"{selected_weeks[0]} ~ {selected_weeks[1]}"
else:
    period_str = "선택 없음"

# 플랫폼 표현 결정
if len(selected_platforms) == len(platforms):
    platform_str = "전체"
elif len(selected_platforms) == 0:
    platform_str = "선택 없음"
else:
    platform_str = ", ".join(selected_platforms)

# 유입 경로 표현 결정
if len(selected_traffic_sources) == len(traffic_sources):
    traffic_str = "전체"
elif len(selected_traffic_sources) == 0:
    traffic_str = "선택 없음"
else:
    traffic_str = ", ".join(selected_traffic_sources)

st.caption(f"🔎 보고 있는 조건: 기간 ({period_str}) · 플랫폼 ({platform_str}) · 유입 경로 ({traffic_str})")

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

# ------------------------------------------------------------
# 화면 탭 구성
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action = st.tabs(["📊 개요", "🔍 탐색", "✅ 액션"])

with tab_overview:
    st.caption("이 탭에서 볼 수 있는 것: 전체 KPI 요약, 자동 계산된 평소 범위 비교, 전체 AI 해설 및 주요 전환율/퍼널 차트")

    # 2. KPI 카드 및 규칙 기반 설명 문장
    def rate(a, b):
        return a / b * 100 if b else 0

    t = f[["세션수", "결제시작세션", "주문세션"]].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("세션 수", f"{t['세션수']:,}개")
    c2.metric("주문 전환율 (방문 → 주문)", f"{rate(t['주문세션'], t['세션수']):.1f}%")
    c3.metric("결제 전환율 (결제 시작 → 주문)", f"{rate(t['주문세션'], t['결제시작세션']):.1f}%")

    with st.container(border=True):
        st.markdown("**📏 지금 숫자 읽기**")

        # 규칙 기반 설명 문장
        f_weeks = sorted(f["주시작일"].unique())
        if len(f_weeks) < 4:
            order_msg = "📦 주문 전환율: 비교하려면 선택 기간을 4주 이상 골라 주세요"
            payment_msg = "💳 결제 전환율: 비교하려면 선택 기간을 4주 이상 골라 주세요"
            st.markdown(f"- {order_msg}")
            st.markdown(f"- {payment_msg}")
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
                order_msg = f"📦 주문 전환율 ({start_str} 주부터 4주): 기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요"
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
                order_msg = f"📦 주문 전환율 ({start_str} 주부터 4주): 이번 값 {curr_order_rate:.1f}%는 {status_o}"
                
            # 결제 전환율 문장
            if len(payment_rates) < 4:
                payment_msg = f"💳 결제 전환율 ({start_str} 주부터 4주): 기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요"
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
                payment_msg = f"💳 결제 전환율 ({start_str} 주부터 4주): 이번 값 {curr_payment_rate:.1f}%는 {status_p}"

            st.markdown(f"- {order_msg}")
            st.markdown(f"- {payment_msg}")

        st.caption(f"📏 자동 계산 · 평소 범위 기준 기간 {baseline_weeks[0]} ~ {baseline_weeks[1]} (주별 값)")

        with st.expander("🤖 AI 해설 · 전체", expanded=False):
            st.write(AI_NOTES.get("전체", "이 집단의 해설은 아직 없습니다."))
        st.caption(AI_SOURCE)

    st.divider()

    # 3. 차트
    left, right = st.columns(2)

    # 3-1. 주별 결제 전환율 (플랫폼별)
    wp = f.groupby(["주시작일", "플랫폼"])[["결제시작세션", "주문세션"]].sum().reset_index()
    wp["결제전환율"] = (wp["주문세션"] / wp["결제시작세션"] * 100).round(1)
    fig1 = px.line(wp, x="주시작일", y="결제전환율", color="플랫폼", markers=True,
                   title="주별 결제 전환율 (플랫폼별)")
    fig1.update_layout(
        xaxis_title="주시작일",
        yaxis_title="결제 전환율 (%)",
        legend_title="플랫폼"
    )
    left.plotly_chart(fig1, width="stretch")

    # 3-2. 세션 퍼널
    steps = {"1.방문": "세션수", "2.식당조회": "조회세션", "3.장바구니": "장바구니세션",
             "4.결제시작": "결제시작세션", "5.주문완료": "주문세션"}
    fun = pd.DataFrame({"단계": list(steps), "세션수": [f[c].sum() for c in steps.values()]})
    fig2 = px.funnel(fun, x="세션수", y="단계", title="세션 퍼널")
    fig2.update_layout(
        xaxis_title="세션 수(개)",
        yaxis_title="퍼널 단계"
    )
    right.plotly_chart(fig2, width="stretch")

    # 3-3. 유입 경로별 주문 전환율
    ws = f.groupby("유입경로")[["세션수", "주문세션"]].sum().reset_index()
    ws["주문전환율"] = (ws["주문세션"] / ws["세션수"] * 100).round(1)
    ws = ws.sort_values("주문전환율")
    fig3 = px.bar(ws, x="주문전환율", y="유입경로", orientation="h", text_auto=".1f",
                  hover_data={"세션수": ":,개"}, title="유입 경로별 주문 전환율")
    fig3.update_xaxes(range=[0, ws["주문전환율"].max() * 1.2] if not ws.empty else [0, 100])
    fig3.update_layout(
        xaxis_title="주문 전환율 (%)",
        yaxis_title="유입 경로"
    )
    # 호버 데이터 한글 레이아웃 설정
    fig3.update_traces(hovertemplate="유입경로=%{y}<br>주문전환율=%{x:.1f}%<br>세션수=%{customdata[0]:,d}개<extra></extra>")
    st.plotly_chart(fig3, width="stretch")

with tab_explore:
    st.caption("이 탭에서 볼 수 있는 것: 플랫폼 및 유입 경로별 집단 비교, 드릴다운 분석 차트, 그리고 선택 집단별 AI 해설")

    # ------------------------------------------------------------
    # 4. 집단 비교 영역
    # ------------------------------------------------------------
    st.subheader("집단 비교")
    group_col = st.radio("비교 기준", ["플랫폼", "유입경로"], horizontal=True)

    # 집단별 5단계 퍼널 데이터 집계
    group_agg = f.groupby(group_col)[["세션수", "조회세션", "장바구니세션", "결제시작세션", "주문세션"]].sum().reset_index()

    # 세션 수가 500개 미만인 집단 확인
    low_session_groups = group_agg[group_agg["세션수"] < 500][group_col].tolist()

    if exclude_low_session and low_session_groups:
        group_agg = group_agg[~group_agg[group_col].isin(low_session_groups)]
        st.caption(f"제외한 집단: {', '.join(low_session_groups)} (세션 500개 미만)")
    elif low_session_groups:
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
                "직전단계세션수": prev_val
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
            hover_data={"세션수": ":,개"}
        )
        fig_group_funnel.update_layout(
            xaxis_title="누적 전환율 (%)",
            yaxis_title="퍼널 단계",
            legend_title=group_col
        )
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
            hover_data={"직전단계세션수": ":,개"},
            title=f"집단별 단계 전환율 ({group_col} 기준)"
        )
        fig_group_bar.update_layout(
            xaxis_title="단계 전환율 (%)",
            yaxis_title="전환 단계",
            legend_title=group_col
        )
        # 호버 툴팁 라벨 명시적 치환
        fig_group_bar.update_traces(hovertemplate="단계전환=%{y}<br>단계전환율=%{x:.1f}%<br>직전 단계 세션 수(개)=%{customdata[0]:,d}개<extra></extra>")
        col_right.plotly_chart(fig_group_bar, width="stretch")

    st.divider()

    # ------------------------------------------------------------
    # 5. 드릴다운: 언제부터, 무엇 때문일까
    # ------------------------------------------------------------
    st.subheader("드릴다운: 언제부터, 무엇 때문일까")

    # f에 존재하는 해당 기준의 값 목록 추출 (체크 시 500개 미만 집단 제외 적용)
    if exclude_low_session and low_session_groups:
        available_groups = sorted([g_val for g_val in f[group_col].unique() if g_val not in low_session_groups])
    else:
        available_groups = sorted(f[group_col].unique())

    if not available_groups:
        st.warning("조건에 해당하는 집단이 없습니다.")
    else:
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
        fig_drill1.update_layout(
            xaxis_title="주시작일",
            yaxis_title="전환율 (%)",
            legend_title=other_col
        )
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
        fig_drill2.update_layout(
            xaxis_title="주시작일",
            yaxis_title="결제 실패 건수(건)",
            legend_title="그룹 구분"
        )
        st.plotly_chart(fig_drill2, width="stretch")
        st.caption("문제가 생긴 시점과 함께 움직였다면 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다.")

        # 2. 드릴다운 차트 아래에 target(선택한 집단)별 AI 해설 및 캡션 추가
        target = selected_sub_group
        with st.expander(f"🤖 AI 해설 · {target}", expanded=False):
            st.write(AI_NOTES.get(target, "이 집단의 해설은 아직 없습니다."))
        st.caption(AI_SOURCE)

with tab_action:
    st.caption("이 탭에서 볼 수 있는 것: 주요 데이터 분석 결과를 바탕으로 도출된 개선 액션 카드 및 현재 측정 KPI 값")
    st.caption("측정 KPI의 현재 값은 선택한 기간의 마지막 4주로 계산합니다")

    # 선택된 기간의 마지막 4주 데이터 구하기 (사이드바 기간 기준, 플랫폼/유입경로 필터 무시)
    action_weeks = sorted(df["주시작일"].unique())
    # 사이드바에서 선택된 기간 내의 주들 중 필터링
    selected_period_weeks = [w for w in action_weeks if selected_weeks[0] <= w <= selected_weeks[1]]
    
    if len(selected_period_weeks) >= 4:
        calc_weeks = selected_period_weeks[-4:]
    else:
        calc_weeks = selected_period_weeks  # 4주 미만인 경우 가능한 주 전체 사용

    # 전체 df 기준(사이드바 기간 및 마지막 4주 조건 적용, 단 플랫폼/유입경로 필터 제외)
    base_period_df = df[(df["주시작일"] >= selected_weeks[0]) & (df["주시작일"] <= selected_weeks[1])]
    if calc_weeks:
        last4_df = base_period_df[base_period_df["주시작일"].isin(calc_weeks)]
    else:
        last4_df = pd.DataFrame(columns=df.columns)

    # 카드 1 KPI 계산: 플랫폼이 Android인 데이터의 결제 전환율 (주문세션 sum / 결제시작세션 sum)
    android_df = last4_df[last4_df["플랫폼"] == "Android"]
    android_payment_sum = android_df["결제시작세션"].sum()
    android_order_sum = android_df["주문세션"].sum()
    if android_payment_sum > 0:
        card1_val = f"{(android_order_sum / android_payment_sum * 100):.1f}%"
    else:
        card1_val = "데이터 없음"

    # 카드 2 KPI 계산: 유입경로가 push인 데이터의 방문 → 식당 조회 전환율 (조회세션 sum / 세션수 sum)
    push_df = last4_df[last4_df["유입경로"] == "push"]
    push_session_sum = push_df["세션수"].sum()
    push_view_sum = push_df["조회세션"].sum()
    if push_session_sum > 0:
        card2_val = f"{(push_view_sum / push_session_sum * 100):.1f}%"
    else:
        card2_val = "데이터 없음"

    col_action1, col_action2 = st.columns(2)

    with col_action1:
        with st.container(border=True):
            st.markdown("**카드 1 · Android 결제**")
            st.markdown("- 문제: 결제 시작에서 주문 전환율이 2026-04-13 주부터 하락해 5월 3주차(5/18 주) 최저 41.4%, 6월 1주차부터 회복")
            st.markdown("- 근거: 같은 기간 iOS는 평소 범위 안 · Android 결제 실패가 주 4건 이하 → 최대 153건")
            st.markdown("- 성격: 특정 시점에 발생한 문제")
            st.markdown("- 액션: 추가 분석: 앱 버전별로 결제 실패 확인 → 모니터링: 결제 전환율이 평소 범위를 벗어나는지 매주 점검")
            st.markdown("- 우선순위: 영향 큼 · 실행 쉬움 → 바로 실행")
            
            st.metric("결제 전환율 (Android · 최근 4주)", card1_val)
            st.caption("대시보드에서: 개요 탭 규칙 문장(플랫폼 Android) · 탐색 탭 드릴다운")

    with col_action2:
        with st.container(border=True):
            st.markdown("**카드 2 · push 방문**")
            st.markdown("- 문제: 방문 → 식당 조회 전환율 28.4% (다른 경로 57-69%), 주문 전환율 3.2%")
            st.markdown("- 근거: 2026년 2월부터 월별 세션의 30-36%가 push · 기간 내내 25-35%")
            st.markdown("- 성격: 전체 구간에 따라 세그먼트별 반응 차이")
            st.markdown("- 액션: 실험: 일부 사용자에게만 푸시 대상과 문구를 바꿔 방문 → 식당 조회 전환율 비교")
            st.markdown("- 우선순위: 효과 불확실 · 실행 쉬움 → 실험부터")
            
            st.metric("방문 → 식당 조회 전환율 (push · 최근 4주)", card2_val)
            st.caption("대시보드에서: 탐색 탭 비교 기준 '유입경로' · 드릴다운 push")
