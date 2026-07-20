import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { settingsQuery } from "../api/queries";
import { StatusMessage } from "../components/StatusMessage";
import { formatLocalDateTime } from "../lib/dateTime";

function timestamp(value: string | null): ReactNode {
  return value === null ? (
    "暂无记录"
  ) : (
    <time dateTime={value}>{formatLocalDateTime(value)}</time>
  );
}

export function SettingsPage() {
  const status = useQuery(settingsQuery());
  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading">设置与状态</h1>
      <p>这里只显示脱敏后的本地运行状态，不展示端点、密钥或投资组合数据。</p>
      {status.isPending ? <p role="status">正在加载本地状态…</p> : null}
      {status.isError ? (
        <StatusMessage
          notice={{ kind: "error", title: "暂时无法加载本地状态，请重试" }}
        />
      ) : null}
      {status.data === undefined ? null : (
        <div className="settings-grid">
          <section
            className="settings-card"
            aria-labelledby="database-status-heading"
          >
            <h2 id="database-status-heading">本地数据库</h2>
            <dl>
              <div>
                <dt>位置</dt>
                <dd>
                  <code>{status.data.database_path}</code>
                </dd>
              </div>
              <div>
                <dt>架构</dt>
                <dd>架构版本 {status.data.schema_revision}</dd>
              </div>
              <div>
                <dt>迁移</dt>
                <dd>
                  {status.data.migration_healthy ? "迁移健康" : "迁移需要检查"}
                </dd>
              </div>
            </dl>
          </section>
          <section
            className="settings-card"
            aria-labelledby="provider-status-heading"
          >
            <h2 id="provider-status-heading">行情适配器</h2>
            <ul>
              {status.data.adapters.map((adapter) => (
                <li key={adapter.name}>
                  {adapter.name}：{adapter.enabled ? "已启用" : "已禁用"}
                </li>
              ))}
            </ul>
            <p>
              上次成功刷新：{timestamp(status.data.last_successful_refresh_at)}
            </p>
          </section>
          <section
            className="settings-card"
            aria-labelledby="cache-status-heading"
          >
            <h2 id="cache-status-heading">本地缓存</h2>
            <p>最新报价缓存 {status.data.latest_quote_count} 项</p>
            <p>候选缓存 {status.data.candidate_cache_count} 项</p>
          </section>
          <section className="settings-card" aria-labelledby="recovery-heading">
            <h2 id="recovery-heading">恢复提示</h2>
            <p>
              自动迁移前备份目录：<code>{status.data.backup_directory}</code>
            </p>
            <p>最近备份：{timestamp(status.data.latest_backup_at)}</p>
            <p>恢复前请停止本地服务，并保留当前数据库与备份副本。</p>
          </section>
        </div>
      )}
    </section>
  );
}
