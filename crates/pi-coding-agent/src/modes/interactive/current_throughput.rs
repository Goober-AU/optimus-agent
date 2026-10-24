//! Rates from fresh counter deltas, excluding the attach baseline and final usage corrections.
use std::collections::VecDeque;
use std::time::{Duration, Instant};

const WINDOW: Duration = Duration::from_secs(2);
const STALE: Duration = Duration::from_secs(3);
const MIN_INTERVAL: Duration = Duration::from_millis(250);

#[derive(Default)]
pub(super) struct LiveWindow {
    points: VecDeque<(Instant, f64, f64)>,
}

impl LiveWindow {
    pub(super) fn observe(&mut self, tokens: f64, streaming_ms: f64, now: Instant) {
        if let Some(&(at, previous_tokens, previous_ms)) = self.points.back() {
            if tokens < previous_tokens || streaming_ms < previous_ms || now < at {
                self.points.clear();
            } else if tokens == previous_tokens && streaming_ms == previous_ms {
                return;
            }
        }
        self.points.push_back((now, tokens, streaming_ms));
        while self.points.len() > 2
            && self.points.get(1).is_some_and(|point| now.saturating_duration_since(point.0) >= WINDOW)
        {
            self.points.pop_front();
        }
        // A worker normally publishes at 4 Hz; keep malicious or unusually fast
        // snapshots from growing a renderer-owned history without bound.
        while self.points.len() > 64 { self.points.pop_front(); }
    }

    pub(super) fn rate(&self, now: Instant) -> Option<f64> {
        let &(last_at, last_tokens, last_ms) = self.points.back()?;
        if now.saturating_duration_since(last_at) >= STALE { return Some(0.0); }
        if self.points.len() < 2 { return None; }
        let &(first_at, first_tokens, first_ms) = self.points.front()?;
        // Receipt time bounds buffered bursts; producer time bounds delayed UI
        // consumption. Include time since the last chunk so stalls decay.
        let elapsed = now.saturating_duration_since(first_at).as_secs_f64()
            .max((last_ms - first_ms) / 1000.0);
        if elapsed < MIN_INTERVAL.as_secs_f64() { return None; }
        let rate = (last_tokens - first_tokens) / elapsed;
        (rate.is_finite() && rate >= 0.0).then_some(rate)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_rate_excludes_history_and_the_buffered_first_chunk() {
        let now = Instant::now();
        let mut window = LiveWindow::default();
        window.observe(10000.0, 100000.0, now);
        assert_eq!(window.rate(now), None);
        window.observe(10040.0, 100500.0, now + Duration::from_millis(500));
        assert_eq!(window.rate(now + Duration::from_millis(500)), Some(80.0));
        assert_eq!(window.rate(now + Duration::from_secs(1)), Some(40.0));
        assert_eq!(window.rate(now + Duration::from_secs(4)), Some(0.0));
    }

    #[test]
    fn current_rate_rejects_tiny_windows_replays_and_counter_resets() {
        let now = Instant::now();
        let mut window = LiveWindow::default();
        window.observe(100.0, 1000.0, now);
        window.observe(200.0, 1001.0, now + Duration::from_millis(1));
        assert_eq!(window.rate(now + Duration::from_millis(1)), None);
        window.observe(200.0, 1001.0, now + Duration::from_secs(2));
        assert_eq!(window.rate(now + Duration::from_secs(4)), Some(0.0));
        window.observe(10.0, 100.0, now + Duration::from_secs(5));
        assert_eq!(window.rate(now + Duration::from_secs(5)), None);
    }

    #[test]
    fn current_rate_tracks_recent_speed_instead_of_lifetime_average() {
        let now = Instant::now();
        let mut window = LiveWindow::default();
        for n in 0..=10 {
            window.observe(n as f64 * 100.0, n as f64 * 1000.0, now + Duration::from_secs(n));
        }
        window.observe(1020.0, 11000.0, now + Duration::from_secs(11));
        window.observe(1040.0, 12000.0, now + Duration::from_secs(12));
        assert_eq!(window.rate(now + Duration::from_secs(12)), Some(20.0));
    }
}
