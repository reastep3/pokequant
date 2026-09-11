export type ScreenerRow = {
  id: number; ticker: string; name: string; asset_type: "card" | "sealed"; language: "en" | "ja";
  set_code: string | null; set_name: string | null; number: string | null; rarity: string | null; grade_key: string;
  snapshot_ts: string; market_price: number | null; fair_value: number | null; ev_gap_pct: number | null; ret_30d: number | null;
  vol_90d_ann: number | null; dd_30d: number | null; dd_60d: number | null; dd_90d: number | null;
  recovery_30d_days: number | null; recovery_60d_days: number | null; recovery_90d_days: number | null;
  kelly_fraction: number | null; kelly_fraction_scaled: number | null; expected_return: number | null; kelly_adj_roi: number | null;
  liquidity_score: number | null; screener_score: number | null; arb_spread: number | null; arb_spread_z: number | null;
  pairs_z: number | null; pairs_half_life_days: number | null; cs_bubble_premium_pct: number | null; pop_total: number | null;
  pop_gem_rate: number | null; pop_velocity_30d: number | null; pop_momentum_z: number | null; sealed_ev: number | null;
  sealed_ev_ratio: number | null; sealed_p_profit: number | null; underwater_days: number | null;
};
export type Screener = { as_of: string; sort: string; desc: boolean; count: number; rows: ScreenerRow[] };

export type Bar = { time: string; value: number; low: number | null; high: number | null; volume: number | null; source: string };

export type Signal = {
  id: number; asset_id: number; ticker: string | null; name: string | null; grade_key: string | null; kind: string;
  direction: "long" | "short" | "flat"; strength: number; headline: string; payload: Record<string, number | null> | null;
  status: string; created_at: string | null; expires_at: string | null;
};

export type DrawdownStat = { window_days: number; max_drawdown: number; current_drawdown: number; peak_day: number; trough_day: number; recovery_days: number | null; underwater_days: number };

export type AssetDetail = {
  asset: Omit<ScreenerRow, "grade_key" | "snapshot_ts"> & { canonical_key: string; image_url: string | null };
  analytics: (Partial<ScreenerRow> & {
    grade_key: string; ts: string; fair_value_lo: number | null; fair_value_hi: number | null; cs_fundamental_value: number | null;
    cs_bubble_share: number | null;
    payload: {
      fair_value?: { grid_t: number[]; fitted: number[]; lower: number[]; upper: number[]; bandwidth_days: number; n_obs: number; method: string; z_market_vs_fair: number | null; resid_std: number };
      drawdowns?: Record<string, DrawdownStat>;
      grading_arb?: { raw_price: number; all_in_cost: number; expected_graded_value: number; spread: number; spread_pct: number; breakeven_gem_rate: number | null; per_grade: Record<string, { prob: number; gross: number; net: number }> };
      arb_signal?: { z: number; mean: number; std: number; direction: string };
      kelly_acquisition?: { f_star: number; f_scaled: number; growth_rate: number; expected_return: number; variance: number; win_prob: number; note: string; outcomes: { label: string; prob: number; payoff: number; ret: number }[] };
      kelly_holding?: { f_star: number; f_scaled: number; mu_h: number; sigma2_h: number; growth_rate: number; expected_return: number };
      pairs?: { alpha: number; beta: number; z: number; mean: number; std: number; lam: number; half_life_days: number | null; r2: number };
      campbell_shiller?: { rho: number; kappa: number; bubble_premium_pct: number; fundamental_value: number; var_share_cf: number; var_share_dr: number; var_share_bubble: number; n_obs: number; stable: boolean; bubble_series: number[] };
      liquidity?: { score: number; components: Record<string, number> };
      sealed_ev?: { price: number; ev: number; ev_ratio: number; mispricing: number; mispricing_pct: number; std: number; p_profit: number; percentiles: Record<string, number>; slot_contributions: { slot: string; expected_hits: number; ev: number; mean_card_value: number; prob_per_pack: number }[]; n_packs: number };
      product?: { type: string; packs: number; msrp: number | null };
      series?: { ts_days: number[]; prices: number[]; t0: string };
      raw_price?: number | null; slab_prices?: Record<string, number>; pop_counts?: Record<string, number>;
    };
  }) | null;
  signals: Signal[];
};

export type SealedRow = ScreenerRow & { ev: AssetDetail["analytics"] extends infer A ? A extends { payload: { sealed_ev?: infer E } } ? E : never : never; product: { type: string; packs: number; msrp: number | null } | null };
