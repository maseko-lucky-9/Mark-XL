//! TelemetryAggregator — read-only SQL aggregation queries.

use crate::store::TelemetryStore;
use openjarvis_core::OpenJarvisError;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AggregateStats {
    pub total_requests: usize,
    pub total_tokens: i64,
    pub avg_latency: f64,
    pub avg_throughput: f64,
    pub total_cost: f64,
    pub total_energy: f64,
}

pub struct TelemetryAggregator;

impl TelemetryAggregator {
    pub fn stats(store: &TelemetryStore) -> Result<AggregateStats, OpenJarvisError> {
        store.query_stats()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use openjarvis_core::TelemetryRecord;

    #[test]
    fn test_stats_empty_table_is_zero() {
        let store = TelemetryStore::in_memory().unwrap();
        let stats = TelemetryAggregator::stats(&store).unwrap();
        assert_eq!(stats.total_requests, 0);
        assert_eq!(stats.total_tokens, 0);
        assert_eq!(stats.avg_latency, 0.0);
        assert_eq!(stats.avg_throughput, 0.0);
        assert_eq!(stats.total_cost, 0.0);
        assert_eq!(stats.total_energy, 0.0);
    }

    #[test]
    fn test_stats_aggregates_records() {
        let store = TelemetryStore::in_memory().unwrap();

        // Three records with known values. Build field-by-field with
        // ..Default::default() to dodge any positional-arg trap.
        let rec1 = TelemetryRecord {
            model_id: "qwen3:8b".into(),
            total_tokens: 100,
            latency_seconds: 1.0,
            throughput_tok_per_sec: 50.0,
            cost_usd: 0.01,
            energy_joules: 5.0,
            ..Default::default()
        };
        let rec2 = TelemetryRecord {
            model_id: "qwen3:8b".into(),
            total_tokens: 200,
            latency_seconds: 2.0,
            throughput_tok_per_sec: 70.0,
            cost_usd: 0.02,
            energy_joules: 7.0,
            ..Default::default()
        };
        let rec3 = TelemetryRecord {
            model_id: "qwen3:8b".into(),
            total_tokens: 300,
            latency_seconds: 3.0,
            throughput_tok_per_sec: 90.0,
            cost_usd: 0.03,
            energy_joules: 9.0,
            ..Default::default()
        };
        store.record(&rec1).unwrap();
        store.record(&rec2).unwrap();
        store.record(&rec3).unwrap();

        let stats = TelemetryAggregator::stats(&store).unwrap();
        assert_eq!(stats.total_requests, 3);
        assert_eq!(stats.total_tokens, 600); // 100 + 200 + 300
        assert!((stats.avg_latency - 2.0).abs() < 1e-9); // (1+2+3)/3
        assert!((stats.avg_throughput - 70.0).abs() < 1e-9); // (50+70+90)/3
        assert!((stats.total_cost - 0.06).abs() < 1e-9); // 0.01+0.02+0.03
        assert!((stats.total_energy - 21.0).abs() < 1e-9); // 5+7+9
    }
}
