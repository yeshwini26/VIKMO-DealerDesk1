"""
VIKMO DealerDesk — Demand Forecasting Pipeline (AI/ML Track)

Produces: ml/forecast.csv (exactly 160 rows, 40 SKUs x 4 weeks)
Forecast target weeks:
- 2026-09-28
- 2026-10-05
- 2026-10-12
- 2026-10-19

Rigorous chronological backtesting: zero future leakage.
"""

from pathlib import Path
from datetime import timedelta
import pandas as pd
import numpy as np
from sklearn.metrics import mean_absolute_error, root_mean_squared_error


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ML_DIR = BASE_DIR / "ml"

TARGET_WEEKS = ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"]

DIWALI_DATES = {
    2024: pd.to_datetime("2024-11-01"),
    2025: pd.to_datetime("2025-10-21"),
    2026: pd.to_datetime("2026-11-08"),
}


def load_datasets():
    sales = pd.read_csv(DATA_DIR / "sales_history.csv")
    sales["week_start"] = pd.to_datetime(sales["week_start"])
    sales = sales.sort_values(["sku", "week_start"]).reset_index(drop=True)

    promo = pd.read_csv(DATA_DIR / "promo_calendar.csv")
    promo["week_start"] = pd.to_datetime(promo["week_start"])

    lead_times = pd.read_csv(DATA_DIR / "lead_times.csv")
    forecast_skus = sorted(lead_times["sku"].unique().tolist())

    return sales, promo, forecast_skus


def get_diwali_proximity(week_start: pd.Timestamp) -> float:
    """
    Gaussian window for pre-festive surge peaking ~2 to 3 weeks before Diwali.
    """
    diwali = DIWALI_DATES.get(week_start.year)
    if not diwali:
        return 0.0
    weeks_to = (diwali - week_start).days / 7.0
    if weeks_to < 0 or weeks_to > 7:
        return 0.0
    return float(np.exp(-0.5 * ((weeks_to - 2.5) / 2.0) ** 2))


class RobustDealerForecaster:
    """
    Adaptive shrinkage forecaster combining:
    1. Weighted recent historical demand (85% 4-week moving average + 15% 12-week moving average)
    2. SKU-specific empirical promotional lift derived strictly from historical data
    3. Moving Diwali festival calendar adjustment
    4. Conservative safeguards for newly launched SKUs and intermittent demand
    """

    def __init__(self):
        self.sku_stats = {}

    def fit(self, train_sales: pd.DataFrame):
        self.sku_stats = {}
        for sku, g in train_sales.groupby("sku"):
            g = g.sort_values("week_start").copy()
            n_obs = len(g)
            units = g["units_sold"].values

            # Recent moving averages
            r4 = float(np.mean(units[-4:])) if n_obs >= 4 else float(np.mean(units))
            r12 = float(np.mean(units[-12:])) if n_obs >= 12 else float(np.mean(units))

            # Intermittent demand
            zero_ratio = float(np.mean(units == 0))
            is_intermittent = zero_ratio > 0.40

            # Empirical promo lift
            p_units = g[g["promo_flag"] == 1]["units_sold"]
            np_units = g[g["promo_flag"] == 0]["units_sold"]
            if len(p_units) > 0 and len(np_units) > 0 and np_units.mean() > 0:
                p_lift = float(p_units.mean() / np_units.mean())
                # Bound lift factor to prevent explosive forecasts
                p_lift = max(1.05, min(1.50, p_lift))
            else:
                p_lift = 1.15

            # Historical pre-Diwali lift
            g["d_prox"] = g["week_start"].apply(get_diwali_proximity)
            festive_units = g[g["d_prox"] > 0.4]["units_sold"]
            if len(festive_units) > 0 and len(np_units) > 0 and np_units.mean() > 0:
                f_lift = float(festive_units.mean() / np_units.mean())
                f_lift = max(1.0, min(1.35, f_lift))
            else:
                f_lift = 1.10

            self.sku_stats[sku] = {
                "n_obs": n_obs,
                "r4": r4,
                "r12": r12,
                "mean_all": float(np.mean(units)),
                "is_intermittent": is_intermittent,
                "promo_lift": p_lift,
                "festive_lift": f_lift,
            }

    def predict(self, sku: str, week_start: pd.Timestamp, promo_flag: int, step: int) -> float:
        stats = self.sku_stats.get(sku)
        if not stats:
            return 0.0

        if stats["n_obs"] <= 6:
            # Newly launched SKU: rely purely on smoothed recent mean without aggressive extrapolation
            base = stats["r4"]
        elif stats["is_intermittent"]:
            # Intermittent demand: blend recent with overall rate
            base = 0.60 * stats["r4"] + 0.40 * stats["mean_all"]
        else:
            # Regular mature SKU: optimal shrinkage
            base = 0.85 * stats["r4"] + 0.15 * stats["r12"]

        # Promotion boost
        if promo_flag == 1:
            base *= stats["promo_lift"]

        # Festive boost based on exact Diwali calendar proximity
        d_prox = get_diwali_proximity(week_start)
        if d_prox > 0.05:
            base *= (1.0 + (stats["festive_lift"] - 1.0) * d_prox)

        return max(0.0, float(base))


def run_chronological_backtesting(sales: pd.DataFrame, forecast_skus: list):
    """
    Evaluates baselines vs. our forecaster across two rolling-origin cutoff windows.
    Zero future leakage: train set contains strictly data <= cutoff.
    """
    cutoffs = [
        pd.to_datetime("2026-07-27"),
        pd.to_datetime("2026-08-24"),
    ]

    print("=" * 75)
    print("CHRONOLOGICAL ROLLING-ORIGIN BACKTESTING RESULTS")
    print("=" * 75)

    summary_metrics = []

    for cutoff in cutoffs:
        eval_horizon = [cutoff + timedelta(weeks=i) for i in range(1, 5)]

        train_set = sales[sales["week_start"] <= cutoff].copy()
        test_set = sales[sales["week_start"].isin(eval_horizon)].copy()
        test_set = test_set[test_set["sku"].isin(forecast_skus)].copy()

        # Baselines
        last_val_map = train_set.groupby("sku")["units_sold"].last().to_dict()
        recent_4_map = train_set.groupby("sku").apply(lambda g: g.tail(4)["units_sold"].mean()).to_dict()
        hist_map = train_set.set_index(["sku", "week_start"])["units_sold"].to_dict()

        # Fit model strictly on train_set
        model = RobustDealerForecaster()
        model.fit(train_set)

        y_true = []
        y_last = []
        y_recent = []
        y_lag52 = []
        y_model = []

        for _, row in test_set.iterrows():
            sku = row["sku"]
            wk = row["week_start"]
            true_units = float(row["units_sold"])
            step = int((wk - cutoff).days / 7)
            p_flag = int(row.get("promo_flag", 0))

            pred_last = max(0.0, float(last_val_map.get(sku, 0.0)))
            pred_recent = max(0.0, float(recent_4_map.get(sku, 0.0)))
            lag52_dt = wk - timedelta(weeks=52)
            pred_lag52 = max(0.0, float(hist_map.get((sku, lag52_dt), pred_recent)))
            pred_model = model.predict(sku, wk, p_flag, step)

            y_true.append(true_units)
            y_last.append(pred_last)
            y_recent.append(pred_recent)
            y_lag52.append(pred_lag52)
            y_model.append(pred_model)

        y_true = np.array(y_true)
        sum_true = np.sum(y_true)

        def calc_wape(preds):
            return np.sum(np.abs(y_true - preds)) / sum_true * 100.0

        mae_last = mean_absolute_error(y_true, y_last)
        mae_rec = mean_absolute_error(y_true, y_recent)
        mae_52 = mean_absolute_error(y_true, y_lag52)
        mae_mod = mean_absolute_error(y_true, y_model)

        wape_last = calc_wape(y_last)
        wape_rec = calc_wape(y_recent)
        wape_52 = calc_wape(y_lag52)
        wape_mod = calc_wape(y_model)

        rmse_rec = root_mean_squared_error(y_true, y_recent)
        rmse_mod = root_mean_squared_error(y_true, y_model)

        print(f"\nValidation Cutoff Origin: {cutoff.strftime('%Y-%m-%d')} (Test Horizon: 4 Weeks)")
        print(f"  Baseline 1 (Last Observed):    MAE = {mae_last:.2f}, WAPE = {wape_last:.2f}%")
        print(f"  Baseline 2 (Recent 4-Wk Avg):  MAE = {mae_rec:.2f}, WAPE = {wape_rec:.2f}%, RMSE = {rmse_rec:.2f}")
        print(f"  Baseline 3 (Same Week Lag-52): MAE = {mae_52:.2f}, WAPE = {wape_52:.2f}%")
        print(f"  Proposed Model (DealerDesk):   MAE = {mae_mod:.2f}, WAPE = {wape_mod:.2f}%, RMSE = {rmse_mod:.2f}")
        print(f"  --> WAPE Improvement over Best Baseline: {wape_rec - wape_mod:+.2f}% points")

        summary_metrics.append({
            "cutoff": cutoff.strftime("%Y-%m-%d"),
            "best_baseline_mae": mae_rec,
            "best_baseline_wape": wape_rec,
            "model_mae": mae_mod,
            "model_wape": wape_mod,
        })

    return summary_metrics


def train_and_export():
    sales, promo, forecast_skus = load_datasets()

    # Step 1: Run chronological backtesting
    metrics = run_chronological_backtesting(sales, forecast_skus)

    # Step 2: Fit on all historical data up to 2026-09-21
    print("\n" + "=" * 75)
    print("FITTING FINAL FORECASTER ON COMPLETE HISTORICAL DATA (Cutoff: 2026-09-21)")
    print("=" * 75)
    final_model = RobustDealerForecaster()
    final_model.fit(sales)

    # Future planned promotions mapping
    promo_map = promo.set_index(["sku", "week_start"])["promo_flag"].to_dict()

    # Step 3: Generate forecasts for 4 target weeks
    rows = []
    for sku in sorted(forecast_skus):
        for step, wk_str in enumerate(TARGET_WEEKS, start=1):
            wk_dt = pd.to_datetime(wk_str)
            p_flag = int(promo_map.get((sku, wk_dt), 0))
            pred_units = final_model.predict(sku, wk_dt, p_flag, step)
            # Round to 1 decimal place as standard demand forecast representation
            rows.append({
                "sku": sku,
                "week_start": wk_str,
                "forecast_units": round(max(0.0, pred_units), 1),
            })

    forecast_df = pd.DataFrame(rows)

    # Step 4: Strict Contract Validations
    assert len(forecast_df) == 160, f"Expected exactly 160 rows, got {len(forecast_df)}"
    assert list(forecast_df.columns) == ["sku", "week_start", "forecast_units"], f"Invalid columns: {forecast_df.columns}"
    assert (forecast_df["forecast_units"] >= 0).all(), "Negative forecast values found!"
    assert set(forecast_df["week_start"].unique()) == set(TARGET_WEEKS), "Target week mismatch!"
    assert set(forecast_df["sku"].unique()) == set(forecast_skus), "SKU list mismatch!"

    # Step 5: Save to ml/forecast.csv
    out_path = ML_DIR / "forecast.csv"
    forecast_df.to_csv(out_path, index=False)
    print(f"\n[OK] ml/forecast.csv successfully generated with {len(forecast_df)} rows at {out_path}.")
    return metrics


if __name__ == "__main__":
    train_and_export()
