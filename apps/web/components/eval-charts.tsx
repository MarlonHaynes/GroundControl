"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** One accent for "met the bar", one for "did not". Nothing decorative. */
const PASS = "#059669";
const FAIL = "#d97706";

type FieldRow = { field: string; accuracy: number; threshold: number };

export function FieldAccuracyChart({ data }: { data: FieldRow[] }) {
  if (data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={Math.max(180, data.length * 34)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 36, top: 4, bottom: 4 }}>
        <XAxis
          type="number"
          domain={[0, 1]}
          tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
          tick={{ fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="field"
          width={140}
          tick={{ fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          cursor={{ fill: "rgba(127,127,127,0.06)" }}
          formatter={(v) => [`${(Number(v) * 100).toFixed(1)}%`, "accuracy"] as [string, string]}
          contentStyle={{
            fontSize: 12,
            borderRadius: 6,
            border: "1px solid rgba(127,127,127,0.25)",
          }}
        />
        <Bar dataKey="accuracy" radius={[0, 3, 3, 0]} barSize={16}>
          {data.map((row) => (
            <Cell key={row.field} fill={row.accuracy >= row.threshold ? PASS : FAIL} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

type CostRow = { bucket: string; count: number };

export function CostDistributionChart({ data }: { data: CostRow[] }) {
  if (data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
        <XAxis dataKey="bucket" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
        <Tooltip
          cursor={{ fill: "rgba(127,127,127,0.06)" }}
          formatter={(v) => [Number(v), "cases"] as [number, string]}
          contentStyle={{
            fontSize: 12,
            borderRadius: 6,
            border: "1px solid rgba(127,127,127,0.25)",
          }}
        />
        <Bar dataKey="count" fill="#6366f1" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
