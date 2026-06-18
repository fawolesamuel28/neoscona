"use client";
import { useEffect, useState } from "react";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";

export default function KpiBar() {
  const supabase = createClientComponentClient();
  const [stats, setStats] = useState({
    total: 0,
    qualified: 0,
    meetings: 0,
    revenue: 0
  });

  useEffect(() => {
    async function fetchStats() {
      const { data: leads } = await supabase.table("leads").select("*");
      if (leads) {
        setStats({
          total: leads.length,
          qualified: leads.filter(l => l.stage === "qualified").length,
          meetings: leads.filter(l => l.meeting_booked).length,
          revenue: leads.reduce((acc, l) => acc + (l.closing_revenue || 0), 0)
        });
      }
    }
    fetchStats();
  }, []);

  const kpis = [
    { label: "Total Leads", value: stats.total, color: 'var(--primary)' },
    { label: "Qualified", value: stats.qualified, color: '#059669' },
    { label: "Meetings", value: stats.meetings, color: '#D97706' },
    { label: "Revenue", value: `₦${(stats.revenue / 1_000_000).toFixed(1)}M`, color: '#E11D48' },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
      {kpis.map((kpi) => (
        <div key={kpi.label} className="glass-card p-6">
          <p className="text-sm font-medium uppercase tracking-wider" style={{ color: 'var(--copy)' }}>{kpi.label}</p>
          <p className="text-4xl font-semibold mt-2 tracking-tighter" style={{ color: kpi.color }}>{kpi.value}</p>
        </div>
      ))}
    </div>
  );
}
