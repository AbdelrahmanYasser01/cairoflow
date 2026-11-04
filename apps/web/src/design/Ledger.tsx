import type { ReactNode } from "react";

export interface LedgerColumn<Row> {
  key: string;
  label: string;
  /** Columns of the page grid this one spans. The spans add up to the ledger's own width. */
  span: number;
  align?: "start" | "end";
  /** This column names the row. */
  rowHeader?: boolean;
  /** Prose: below 640px its label sits above it, because beside it the text gets a sliver. */
  stack?: boolean;
  cell: (row: Row) => ReactNode;
}

/**
 * A table of record whose columns are columns of the page grid. A ledger inside a seven-column
 * instrument passes spans adding up to seven and lands on the same lines as the page.
 *
 * Roles are explicit because `display: grid` on a table strips its semantics in browsers.
 */
export function Ledger<Row>({
  columns,
  rows,
  rowKey,
  caption,
  className = "",
}: {
  columns: readonly LedgerColumn<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  /** Read by screen readers. */
  caption: string;
  className?: string;
}) {
  const total = columns.reduce((sum, column) => sum + column.span, 0);
  const place = (column: LedgerColumn<Row>) => ({ gridColumn: `span ${column.span}` });
  const edge = (column: LedgerColumn<Row>) =>
    `${column.align === "end" ? "is-end" : ""} ${column.stack ? "is-stack" : ""}`.trim();
  return (
    <table
      role="table"
      aria-label={caption}
      className={`ledger-grid ${className}`}
      style={{ gridTemplateColumns: `repeat(${total}, minmax(0, 1fr))` }}
    >
      <thead role="rowgroup">
        <tr role="row">
          {columns.map((column) => (
            <th key={column.key} role="columnheader" scope="col" className={edge(column)} style={place(column)}>
              {column.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody role="rowgroup">
        {rows.map((row) => (
          <tr key={rowKey(row)} role="row">
            {columns.map((column) =>
              column.rowHeader ? (
                <th key={column.key} role="rowheader" scope="row" className={edge(column)} style={place(column)}>
                  {column.cell(row)}
                </th>
              ) : (
                <td key={column.key} role="cell" data-label={column.label} className={edge(column)} style={place(column)}>
                  {column.cell(row)}
                </td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
