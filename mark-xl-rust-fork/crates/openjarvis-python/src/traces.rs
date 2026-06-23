//! PyO3 bindings for trace types.

use pyo3::prelude::*;
use std::sync::Arc;

#[pyclass(name = "TraceStore")]
pub struct PyTraceStore {
    pub inner: Arc<openjarvis_traces::TraceStore>,
}

#[pymethods]
impl PyTraceStore {
    #[new]
    #[pyo3(signature = (path=None))]
    fn new(path: Option<&str>) -> PyResult<Self> {
        let inner = match path {
            Some(p) => openjarvis_traces::TraceStore::new(std::path::Path::new(p)),
            None => openjarvis_traces::TraceStore::in_memory(),
        }
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        Ok(Self {
            inner: Arc::new(inner),
        })
    }

    fn count(&self) -> PyResult<usize> {
        self.inner
            .count()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    fn save(&self, trace_json: &str) -> PyResult<()> {
        let trace: openjarvis_core::Trace = serde_json::from_str(trace_json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        self.inner
            .save(&trace)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    fn get(&self, trace_id: &str) -> PyResult<Option<String>> {
        let result = self
            .inner
            .get(trace_id)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        match result {
            Some(trace) => Ok(Some(
                serde_json::to_string(&trace)
                    .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?,
            )),
            None => Ok(None),
        }
    }

    #[pyo3(signature = (limit=100, offset=0))]
    fn list_traces(&self, limit: usize, offset: usize) -> PyResult<Vec<String>> {
        let traces = self
            .inner
            .list_traces(limit, offset)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        traces
            .iter()
            .map(|t| {
                serde_json::to_string(t)
                    .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
            })
            .collect()
    }
}

#[pyclass(name = "TraceCollector")]
pub struct PyTraceCollector {
    inner: openjarvis_traces::TraceCollector,
}

#[pymethods]
impl PyTraceCollector {
    #[new]
    fn new(store: &PyTraceStore) -> Self {
        Self {
            inner: openjarvis_traces::TraceCollector::new(Arc::clone(&store.inner)),
        }
    }

    fn active_count(&self) -> usize {
        self.inner.active_count()
    }

    fn start_trace(&self, trace_id: &str, query: &str, agent: &str, model: &str) {
        self.inner.start_trace(trace_id, query, agent, model);
    }

    #[pyo3(signature = (trace_id, step_json))]
    fn add_step(&self, trace_id: &str, step_json: &str) -> PyResult<()> {
        let step: openjarvis_core::TraceStep = serde_json::from_str(step_json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        self.inner.add_step(trace_id, step);
        Ok(())
    }

    #[pyo3(signature = (trace_id, result, outcome=None))]
    fn end_trace(&self, trace_id: &str, result: &str, outcome: Option<&str>) -> PyResult<()> {
        self.inner
            .end_trace(trace_id, result, outcome)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }
}

/// TraceAnalyzer wraps stats computation over a TraceStore.
/// Since the Rust TraceAnalyzer has a lifetime parameter, we own the store
/// and create the analyzer on each call.
#[pyclass(name = "TraceAnalyzer")]
pub struct PyTraceAnalyzer {
    store: Arc<openjarvis_traces::TraceStore>,
}

#[pymethods]
impl PyTraceAnalyzer {
    #[new]
    fn new(store: &PyTraceStore) -> Self {
        Self {
            store: Arc::clone(&store.inner),
        }
    }

    fn stats(&self) -> PyResult<String> {
        let analyzer = openjarvis_traces::TraceAnalyzer::new(&self.store);
        let stats = analyzer
            .overall_stats()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        Ok(serde_json::to_string(&stats).unwrap_or_default())
    }

    fn stats_by_agent(&self) -> PyResult<String> {
        let analyzer = openjarvis_traces::TraceAnalyzer::new(&self.store);
        let stats = analyzer
            .stats_by_agent()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        Ok(serde_json::to_string(&stats).unwrap_or_default())
    }

    fn stats_by_model(&self) -> PyResult<String> {
        let analyzer = openjarvis_traces::TraceAnalyzer::new(&self.store);
        let stats = analyzer
            .stats_by_model()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        Ok(serde_json::to_string(&stats).unwrap_or_default())
    }
}
