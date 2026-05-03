import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from api_client import PolymarketClient
from math_logic import PortfolioMath

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Quant Portfolio Manager", page_icon="🦈", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FAFAFA; }
    div[data-testid="metric-container"] {
        background-color: #1E212B; border: 1px solid #2D3139; padding: 5%; border-radius: 10px;
    }
    h1, h2, h3 { color: #00D2FF !important; font-family: 'Courier New', Courier, monospace; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# SESSION STATE (The Shopping Cart)
# ==========================================
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = [] 
if 'current_options' not in st.session_state:
    st.session_state.current_options = []

# ==========================================
# SIDEBAR
# ==========================================
with st.sidebar:
    st.markdown("### 🏦 Global Settings")
    bankroll = st.number_input("Total Bankroll ($)", min_value=100, value=1000, step=100)
    hurdle_rate = st.slider("Hurdle Rate (Annual %)", 1.0, 20.0, 4.5, 0.5) / 100.0
    
    # Global Kelly fraction: scales all final allocations
    kelly_fraction_val = st.slider("Kelly Fraction (%)", 5, 100, 50, step=5) / 100.0
    
    st.markdown("---")
    st.markdown(f"**Bets in Portfolio:** {len(st.session_state.portfolio)}")
    if st.button("🗑️ Clear Portfolio"):
        for key in list(st.session_state.keys()):
            if key.startswith("corr_"):
                del st.session_state[key]
        st.session_state.portfolio = []
        st.rerun()

st.title("📊 Simultaneous Kelly Optimizer")

tab1, tab2 = st.tabs(["🔍 1. Find & Add Bets", "🧠 2. Optimize Portfolio"])

# ==========================================
# TAB 1: ADD BETS
# ==========================================
with tab1:
    st.markdown("Search for Polymarket events and add the ones with positive EV to your portfolio.")
    url_input = st.text_input("Paste Polymarket URL:", placeholder="https://polymarket.com/...")
    
    if st.button("Scan URL"):
        with st.spinner("Scanning..."):
            client = PolymarketClient()
            st.session_state.current_options = client.get_all_options_from_url(url_input)
            
    if st.session_state.current_options:
        st.markdown("---")
        option_dict = {f"{opt['outcome']} ({opt['market_question']})": opt for opt in st.session_state.current_options}
        
        col1, col2 = st.columns([2, 1])
        with col1:
            selected_key = st.selectbox("Select outcome:", list(option_dict.keys()))
            selected_option = option_dict[selected_key]
        with col2:
            true_prob_pct = st.number_input("Your True Probability (%)", min_value=1, max_value=99, value=50)
            true_prob = true_prob_pct / 100.0

        if st.button("➕ Add to Portfolio", type="primary"):
            client = PolymarketClient()
            market_data = client.get_order_book(selected_option["token_id"])
            
            if market_data:
                market_data["end_date"] = selected_option["end_date"]
                math_engine = PortfolioMath(default_hurdle_rate=hurdle_rate)
                analysis = math_engine.evaluate_bet(true_prob, market_data, hurdle_rate)
                
                st.session_state.portfolio.append({
                    "name": f"{selected_option['market_question'][:50]}... -> {selected_option['outcome']}",
                    "outcome": selected_option['outcome'],
                    "true_prob": true_prob,
                    "market_data": market_data,
                    "market_question": selected_option['market_question'],
                    "event_slug": selected_option.get('event_slug', ''),
                    "analysis": analysis
                })
                if analysis.get('date_fallback_used'):
                    st.toast("Added ✅ — Warning: using 30-day fallback for end date", icon="⚠️")
                else:
                    st.toast("✅ Added to Portfolio successfully!")
                st.rerun()
            else:
                st.error("Market lacks liquidity.")

# ==========================================
# TAB 2: PORTFOLIO OPTIMIZATION
# ==========================================
with tab2:
    if len(st.session_state.portfolio) < 2:
        st.info("Please add at least 2 bets from the first tab to run the covariance optimization.")
    else:
        st.markdown("### Your Current Alpha Ideas")
        
        df_data = []
        for idx, bet in enumerate(st.session_state.portfolio):
            df_data.append({
                "Market": bet["name"],
                "Your Prob": f"{bet['true_prob']*100:.1f}%",
                "Market Price": f"${bet['market_data']['best_ask']:.3f}",
                "Expected Value": f"${bet['analysis']['expected_value']:.3f}",
                "Full Kelly": f"{bet['analysis']['recommended_bankroll_pct']*100:.1f}%",
                "Viable?": "✅" if bet['analysis']['is_viable'] else "❌"
            })
        st.table(pd.DataFrame(df_data))
        st.caption("Viable = positive EV **and** annualized ROI ≥ hurdle rate **and** spread < 5¢")
        
        st.markdown("---")

        # ==========================================
        # CORRELATION SETTINGS
        # ==========================================
        st.markdown("### 🔗 Bet Correlations")
        st.caption("-1 = opposite outcomes · 0 = independent · +1 = move together")

        math_engine = PortfolioMath()
        n = len(st.session_state.portfolio)
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        correlations = {}

        with st.expander(f"Adjust {len(pairs)} pair{'s' if len(pairs) != 1 else ''}", expanded=len(pairs) <= 6):
            for row_start in range(0, len(pairs), 2):
                row_pairs = pairs[row_start:row_start + 2]
                cols = st.columns(2)
                for col_idx, (i, j) in enumerate(row_pairs):
                    with cols[col_idx]:
                        bet_i = st.session_state.portfolio[i]
                        bet_j = st.session_state.portfolio[j]

                        # Smart default from structural analysis
                        default_corr = math_engine.get_structural_correlation(bet_i, bet_j)

                        same_mkt = default_corr != 0.0
                        tag = "🔗" if same_mkt else "↔"
                        label = f"{tag} #{i} vs #{j}"

                        val = st.slider(
                            label, -1.0, 1.0, float(default_corr), 0.05,
                            key=f"corr_{i}_{j}",
                            help=f"{bet_i['name']} ↔ {bet_j['name']}"
                        )
                        correlations[(i, j)] = val

        st.markdown("---")
        st.caption(f"Risk scaling: **{int(kelly_fraction_val * 100)}% Kelly** (set in sidebar)")

        if st.button("🚀 Run Simultaneous Kelly Optimization", type="primary", use_container_width=True):
            with st.spinner("Calculating Covariance Matrix and optimizing weights..."):

                weights, cov_matrix = math_engine.optimize_portfolio(
                    st.session_state.portfolio,
                    kelly_fraction=kelly_fraction_val,
                    correlations=correlations
                )

                st.markdown("### 🏆 Allocation Results")

                labels = []
                values = []
                cash_weight = 1.0

                for i, weight in enumerate(weights):
                    if weight > 0.001:
                        labels.append(f"#{i} {st.session_state.portfolio[i]['name']}")
                        values.append(weight)
                        cash_weight -= weight

                labels.append("Cash (Risk-Free)")
                values.append(max(0, cash_weight))

                c1, c2 = st.columns([1, 1])
                with c1:
                    fig_pie = px.pie(names=labels, values=values, title="Optimal Portfolio Allocation", hole=0.4,
                                     color_discrete_sequence=px.colors.sequential.Tealgrn)
                    fig_pie.update_traces(hovertemplate='%{label}<br>%{percent}<extra></extra>')
                    fig_pie.update_layout(paper_bgcolor="rgba(0,0,0,0)", font={'color': "white"})
                    st.plotly_chart(fig_pie, use_container_width=True)

                with c2:
                    names = [f"#{i} {bet['name'][:30]}..." for i, bet in enumerate(st.session_state.portfolio)]
                    fig_cov = px.imshow(cov_matrix, x=names, y=names,
                                        color_continuous_scale="RdBu_r", title="Asset Covariance Matrix")
                    fig_cov.update_layout(paper_bgcolor="rgba(0,0,0,0)", font={'color': "white"})
                    st.plotly_chart(fig_cov, use_container_width=True)

                st.markdown("### 💰 Execution Orders")
                for i, weight in enumerate(weights):
                    if weight > 0.001:
                        bet_amount = bankroll * weight
                        bet_info = st.session_state.portfolio[i]
                        is_viable = bet_info['analysis']['is_viable']
                        if is_viable:
                            st.success(f"**BUY:** ${bet_amount:.2f} of **#{i} {bet_info['name']}**")
                        else:
                            st.warning(f"⚠️ **BUY:** ${bet_amount:.2f} of **#{i} {bet_info['name']}** — Not viable (low ROI or wide spread)")