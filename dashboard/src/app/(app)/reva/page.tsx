"use client";

import { useState, useEffect } from "react";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import KanbanBoard from "@/components/KanbanBoard";
import KpiBar from "@/components/KpiBar";
import LeadDetailModal from "@/components/LeadDetailModal";
import PropertyManager from "@/components/PropertyManager";
import { MessageSquare, Zap } from "lucide-react";

export default function RevaDashboardPage() {
  const [activeTab, setActiveTab] = useState<"leads" | "properties">("leads");
  const [selectedLead, setSelectedLead] = useState<any>(null);
  const supabase = createClientComponentClient();
  const [user, setUser] = useState<any>(null);

  useEffect(() => {
    const checkAuth = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      setUser(session?.user ?? null);
    };
    checkAuth();
  }, [supabase]);

  return (
    <div>
      {/* Header */}
      <div className="border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="max-w-7xl mx-auto px-6 py-8">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold" style={{ color: 'var(--heading)', letterSpacing: '-0.03em' }}>
                Neoscona Reva
              </h1>
              <div className="flex items-center gap-2 px-3 py-1 rounded-full" style={{ backgroundColor: 'var(--primary-alt)', color: 'var(--primary)' }}>
                <div className="w-2 h-2 rounded-full animate-pulse" style={{ backgroundColor: 'var(--live)' }} />
                <span className="text-xs font-semibold uppercase tracking-wider">Live</span>
              </div>
            </div>
          </div>
          <p style={{ color: 'var(--copy)' }}>
            AI sales engine for real estate — qualify, nurture, and book 24/7
          </p>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* KPI Bar */}
        <div className="mb-8">
          <KpiBar />
        </div>

        {/* Tabs */}
        <div className="flex gap-4 mb-6 border-b" style={{ borderColor: 'var(--border)' }}>
          <button
            onClick={() => setActiveTab("leads")}
            className={`pb-4 px-2 border-b-2 font-medium text-sm transition-colors`}
            style={activeTab === "leads" ? { borderColor: 'var(--primary)', color: 'var(--primary)' } : { borderColor: 'transparent', color: 'var(--copy)' }}
            onMouseOver={(e) => { if (activeTab !== "leads") { e.currentTarget.style.color = 'var(--heading)' } }}
            onMouseOut={(e) => { if (activeTab !== "leads") { e.currentTarget.style.color = 'var(--copy)' } }}
          >
            Leads
          </button>
          <button
            onClick={() => setActiveTab("properties")}
            className={`pb-4 px-2 border-b-2 font-medium text-sm transition-colors`}
            style={activeTab === "properties" ? { borderColor: 'var(--primary)', color: 'var(--primary)' } : { borderColor: 'transparent', color: 'var(--copy)' }}
            onMouseOver={(e) => { if (activeTab !== "properties") { e.currentTarget.style.color = 'var(--heading)' } }}
            onMouseOut={(e) => { if (activeTab !== "properties") { e.currentTarget.style.color = 'var(--copy)' } }}
          >
            Properties
          </button>
        </div>

        {/* Tab Content */}
        {activeTab === "leads" && (
          <div>
            <KanbanBoard onSelectLead={setSelectedLead} />
          </div>
        )}
        {activeTab === "properties" && <PropertyManager />}

        {/* Lead Detail Modal */}
        {selectedLead && (
          <LeadDetailModal
            lead={selectedLead}
            onClose={() => setSelectedLead(null)}
          />
        )}
      </div>
    </div>
  );
}
