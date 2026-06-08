import type React from 'react';
import { useMemo, useState } from 'react';
import type { StockMetrics } from '../../types/analysis';

interface StockMetricsTableProps {
  metrics: StockMetrics[];
}

type SortField = 'code' | 'currentPrice' | 'changePct' | 'marketCap' | 'dividendYield' | 'volatility';
type SortOrder = 'asc' | 'desc';

export const StockMetricsTable: React.FC<StockMetricsTableProps> = ({ metrics }) => {
  const [sortField, setSortField] = useState<SortField>('code');
  const [sortOrder, setSortOrder] = useState<SortOrder>('asc');

  const sortedMetrics = useMemo(() => {
    const sorted = [...metrics].sort((a, b) => {
      const aVal = a[sortField] ?? 0;
      const bVal = b[sortField] ?? 0;
      
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortOrder === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      
      const aNum = typeof aVal === 'number' ? aVal : 0;
      const bNum = typeof bVal === 'number' ? bVal : 0;
      return sortOrder === 'asc' ? aNum - bNum : bNum - aNum;
    });
    return sorted;
  }, [metrics, sortField, sortOrder]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortOrder('asc');
    }
  };

  const SortIndicator: React.FC<{ field: SortField }> = ({ field }) => {
    if (sortField !== field) return null;
    return <span className="ml-1">{sortOrder === 'asc' ? '↑' : '↓'}</span>;
  };

  const renderPrice = (price?: number) => {
    if (price === undefined || price === null) return '-';
    return `¥${price.toFixed(2)}`;
  };

  const renderPercent = (pct?: number) => {
    if (pct === undefined || pct === null) return '-';
    const sign = pct >= 0 ? '+' : '';
    const color = pct >= 0 ? 'text-green-600' : 'text-red-600';
    return <span className={color}>{sign}{pct.toFixed(2)}%</span>;
  };

  const renderMarketCap = (cap?: number) => {
    if (cap === undefined || cap === null) return '-';
    if (cap >= 1) return `¥${cap.toFixed(2)}万亿`;
    return `¥${(cap * 100).toFixed(2)}亿`;
  };

  const renderValue = (value?: number, suffix: string = '') => {
    if (value === undefined || value === null) return '-';
    return `${value.toFixed(2)}${suffix}`;
  };

  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full">
        <thead className="sticky top-0 bg-subtle-hover">
          <tr>
            <th className="px-4 py-3 text-left text-sm font-semibold text-foreground cursor-pointer hover:bg-subtle-hover transition"
              onClick={() => handleSort('code')}>
              股票代码
              <SortIndicator field="code" />
            </th>
            <th className="px-4 py-3 text-left text-sm font-semibold text-foreground">
              股票名称
            </th>
            <th className="px-4 py-3 text-right text-sm font-semibold text-foreground cursor-pointer hover:bg-subtle-hover transition"
              onClick={() => handleSort('currentPrice')}>
              当前价格
              <SortIndicator field="currentPrice" />
            </th>
            <th className="px-4 py-3 text-right text-sm font-semibold text-foreground cursor-pointer hover:bg-subtle-hover transition"
              onClick={() => handleSort('changePct')}>
              涨跌幅
              <SortIndicator field="changePct" />
            </th>
            <th className="px-4 py-3 text-right text-sm font-semibold text-foreground cursor-pointer hover:bg-subtle-hover transition"
              onClick={() => handleSort('marketCap')}>
              市值
              <SortIndicator field="marketCap" />
            </th>
            <th className="px-4 py-3 text-right text-sm font-semibold text-foreground cursor-pointer hover:bg-subtle-hover transition"
              onClick={() => handleSort('dividendYield')}>
              股息率
              <SortIndicator field="dividendYield" />
            </th>
            <th className="px-4 py-3 text-right text-sm font-semibold text-foreground cursor-pointer hover:bg-subtle-hover transition"
              onClick={() => handleSort('volatility')}>
              波动率
              <SortIndicator field="volatility" />
            </th>
            <th className="px-4 py-3 text-left text-xs text-muted-text">
              最后更新
            </th>
          </tr>
        </thead>
        <tbody>
          {sortedMetrics.map((metric, index) => (
            <tr
              key={metric.code}
              className={`border-t border-subtle-hover hover:bg-subtle-hover transition ${
                index % 2 === 0 ? 'bg-background' : 'bg-card'
              }`}
            >
              <td className="px-4 py-3">
                <span className="font-mono font-semibold text-foreground">{metric.code}</span>
              </td>
              <td className="px-4 py-3 text-sm text-foreground">
                {metric.name || '-'}
              </td>
              <td className="px-4 py-3 text-right text-sm text-foreground">
                {renderPrice(metric.currentPrice)}
              </td>
              <td className="px-4 py-3 text-right text-sm font-semibold">
                {renderPercent(metric.changePct)}
              </td>
              <td className="px-4 py-3 text-right text-sm text-foreground">
                {renderMarketCap(metric.marketCap)}
              </td>
              <td className="px-4 py-3 text-right text-sm text-foreground">
                {renderValue(metric.dividendYield, '%')}
              </td>
              <td className="px-4 py-3 text-right text-sm text-foreground">
                {renderValue(metric.volatility, '%')}
              </td>
              <td className="px-4 py-3 text-xs text-muted-text">
                {metric.updatedAt ? new Date(metric.updatedAt).toLocaleDateString() : '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
