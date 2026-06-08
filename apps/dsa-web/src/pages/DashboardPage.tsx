import type React from 'react';
import { useEffect, useState, useCallback } from 'react';
import { ApiErrorAlert, Button, EmptyState, InlineAlert } from '../components/common';
import { StockMetricsTable } from '../components/dashboard/StockMetricsTable';
import type { StockMetrics, DashboardMetricsResponse } from '../types/analysis';

const DashboardPage: React.FC = () => {
  const [metrics, setMetrics] = useState<StockMetrics[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  useEffect(() => {
    document.title = '股票仪表板 - DSA';
  }, []);

  const loadMetrics = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/v1/stocks/dashboard/metrics');
      if (!response.ok) {
        throw new Error(`Failed to fetch metrics: ${response.statusText}`);
      }
      const data: DashboardMetricsResponse = await response.json();
      setMetrics(data.items);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load metrics');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMetrics();
    // 每 5 分钟自动刷新一次
    const interval = setInterval(() => {
      void loadMetrics();
    }, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [loadMetrics]);

  const handleRefresh = useCallback(() => {
    void loadMetrics();
  }, [loadMetrics]);

  return (
    <div className="h-full flex flex-col gap-4 p-4 bg-background">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">📊 股票仪表板</h1>
          <p className="text-sm text-muted-text mt-1">
            自选股票的关键指标（股息率、市值、波动率）
          </p>
        </div>
        <div className="flex items-center gap-2">
          {lastUpdated && (
            <span className="text-xs text-muted-text">
              最后更新: {lastUpdated}
            </span>
          )}
          <Button
            onClick={handleRefresh}
            disabled={isLoading}
            size="sm"
            variant="outline"
          >
            {isLoading ? '加载中...' : '刷新'}
          </Button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <ApiErrorAlert
          error={error}
          onDismiss={() => setError(null)}
        />
      )}

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {isLoading && metrics.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center">
              <div className="text-4xl mb-4">⏳</div>
              <p className="text-muted-text">加载中...</p>
            </div>
          </div>
        ) : metrics.length === 0 ? (
          <EmptyState
            title="暂无数据"
            description="暂无股票指标数据，请先进行股票分析"
            action={
              <Button onClick={handleRefresh} variant="default" size="sm">
                重新加载
              </Button>
            }
          />
        ) : (
          <div className="h-full overflow-auto bg-card rounded-lg border border-subtle-hover">
            <StockMetricsTable metrics={metrics} />
          </div>
        )}
      </div>

      {/* Info */}
      <div className="text-xs text-muted-text">
        <InlineAlert
          type="info"
          message="仪表板每 5 分钟自动刷新一次，显示最新的股票关键指标。"
        />
      </div>
    </div>
  );
};

export default DashboardPage;
