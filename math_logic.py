from datetime import datetime
from typing import Dict, Any, List, Optional
import numpy as np
from scipy.optimize import minimize

class PortfolioMath:
    """
    Core mathematical engine for prediction markets.
    Handles EV, Kelly Criterion, and Simultaneous Portfolio Optimization
    with user-defined correlations.
    """

    def __init__(self, default_hurdle_rate: float = 0.045):
        self.default_hurdle_rate = default_hurdle_rate

    def calculate_ev(self, true_probability: float, buy_price: float) -> float:
        return true_probability - buy_price

    def calculate_kelly(self, true_probability: float, buy_price: float) -> float:
        """Calculates the Full Kelly stake for a single binary bet at price buy_price."""
        if true_probability <= buy_price or buy_price >= 1.0:
            return 0.0
        return max(0.0, (true_probability - buy_price) / (1.0 - buy_price))

    def get_days_to_expiry(self, end_date_str: str) -> dict:
        """
        Parses end date trying multiple ISO formats.
        Returns {'days': int, 'fallback_used': bool}.
        """
        DATE_FORMATS = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ]

        if not end_date_str or end_date_str == "Unknown Date":
            return {"days": 30, "fallback_used": True}

        clean_date = end_date_str.strip()
        for fmt in DATE_FORMATS:
            try:
                end_date = datetime.strptime(clean_date, fmt)
                days_left = (end_date - datetime.now()).days
                return {"days": max(1, days_left), "fallback_used": False}
            except ValueError:
                continue

        return {"days": 30, "fallback_used": True}

    def calculate_annualized_roi(self, expected_value: float, buy_price: float, days_to_expiry: int) -> float:
        if expected_value <= 0 or buy_price <= 0:
            return 0.0
        roi = expected_value / buy_price 
        years = days_to_expiry / 365.25
        return ((1 + roi) ** (1 / years)) - 1

    def evaluate_bet(self, true_probability: float, market_data: Dict[str, Any], hurdle_rate: Optional[float] = None) -> Dict[str, Any]:
        if hurdle_rate is None:
            hurdle_rate = self.default_hurdle_rate

        buy_price = market_data.get("best_ask", 1.0)
        ev = self.calculate_ev(true_probability, buy_price)
        
        # Full Kelly — fractional scaling is applied globally after portfolio optimization
        kelly = self.calculate_kelly(true_probability, buy_price)
        
        days_info = self.get_days_to_expiry(market_data.get("end_date", ""))
        days = days_info["days"]
        fallback_used = days_info["fallback_used"]
        
        ann_roi = self.calculate_annualized_roi(ev, buy_price, days)
        
        is_viable = (ev > 0) and (ann_roi >= hurdle_rate) and market_data.get("is_tradable", False)
        
        return {
            "expected_value": ev,
            "recommended_bankroll_pct": kelly,
            "annualized_roi": ann_roi,
            "days_locked": days,
            "date_fallback_used": fallback_used,
            "is_viable": is_viable
        }

    def get_structural_correlation(self, bet_a: Dict, bet_b: Dict) -> float:
        """
        Computes a smart default correlation for a pair of bets.
        - Same market (mutually exclusive outcomes): negative correlation
          derived from the joint Bernoulli structure.
        - Different markets/events: 0.0 (independence).
        """
        q_a = bet_a.get('market_question')
        q_b = bet_b.get('market_question')

        if q_a is not None and q_b is not None and q_a == q_b:
            p_a = bet_a['true_prob']
            p_b = bet_b['true_prob']
            denom = (1 - p_a) * (1 - p_b)
            if denom > 0:
                raw = -np.sqrt((p_a * p_b) / denom)
                return round(max(-1.0, raw), 2)
            return -1.0

        return 0.0

    def optimize_portfolio(self, portfolio_bets: List[Dict], kelly_fraction: float = 0.5, correlations: Optional[Dict] = None):
        """
        Simultaneous Kelly Criterion with user-defined correlations.

        - Uses the correlation values provided by the user for each bet pair.
        - Applies Kelly fraction as a final global scaling factor to preserve
          the optimal relative proportions found by the optimizer.
        """
        if correlations is None:
            correlations = {}

        n_bets = len(portfolio_bets)
        if n_bets == 0:
            return None, None

        expected_returns = []
        bounds = []
        
        for bet in portfolio_bets:
            p = bet['true_prob']
            c = bet['market_data']['best_ask']
            
            # ROI per $1 invested
            expected_returns.append((p - c) / c) 
            
            # Upper bound: Full Kelly for this individual bet
            max_indiv_bet = bet['analysis']['recommended_bankroll_pct']
            bounds.append((0.0, max_indiv_bet))
            
        expected_returns = np.array(expected_returns)

        # Build Covariance Matrix from user-provided correlations
        cov_matrix = self._build_covariance_matrix(portfolio_bets, correlations)

        def objective_function(weights):
            portfolio_return = np.dot(weights, expected_returns)
            portfolio_variance = np.dot(weights.T, np.dot(cov_matrix, weights))
            # Maximize log-growth ≈ Return - 0.5 * Variance (Mean-Variance Kelly)
            return -(portfolio_return - 0.5 * portfolio_variance)

        # Full Kelly constraint: sum of weights <= 1.0
        constraints = ({'type': 'ineq', 'fun': lambda w: 1.0 - np.sum(w)})
        initial_guess = np.array([b[1] / 2 for b in bounds])

        result = minimize(objective_function, initial_guess, method='SLSQP', bounds=bounds, constraints=constraints)

        # Apply Kelly fraction as a final global scaling factor
        optimal_weights = result.x * kelly_fraction

        return np.round(optimal_weights, 4), cov_matrix

    def _build_covariance_matrix(self, portfolio_bets: List[Dict], correlations: Dict) -> np.ndarray:
        """
        Builds a symmetric covariance matrix from user-provided correlations.
        Cov[i][j] = correlation(i,j) * std_i * std_j
        where std_i = sqrt(p_i * (1 - p_i)) / c_i (Bernoulli return std dev).
        """
        n = len(portfolio_bets)
        cov_matrix = np.zeros((n, n))

        # Pre-compute standard deviations
        stds = []
        for bet in portfolio_bets:
            p = bet['true_prob']
            c = bet['market_data']['best_ask']
            stds.append(np.sqrt(p * (1 - p)) / c)

        for i in range(n):
            # Diagonal: variance = std²
            cov_matrix[i][i] = stds[i] ** 2

            for j in range(i + 1, n):
                # User-provided correlation (defaults to 0 if not specified)
                corr = correlations.get((i, j), 0.0)
                cov = corr * stds[i] * stds[j]
                cov_matrix[i][j] = cov
                cov_matrix[j][i] = cov

        return cov_matrix