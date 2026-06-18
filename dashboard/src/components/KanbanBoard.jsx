"use client";
import { useEffect, useState } from "react";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import { formatDistanceToNow } from "date-fns";
import { MessageSquare, Phone, Instagram, User, Flame } from "lucide-react";

const STAGE_LABELS = {
  "new": "New",
  "qualifying": "Qualifying",
  "qualified": "Qualified",
  "closed": "Closed"
};

export default function KanbanBoard({ onSelectLead }) {
  const supabase = createClientComponentClient();
  const [leads, setLeads] = useState([]);

  useEffect(() => {
    async function fetchLeads() {
      const { data } = await supabase.table("leads").select("*").order("updated_at", { ascending: false });
      if (data) setLeads(data);
    }
    fetchLeads();

    // Subscribe to real-time changes
    const channel = supabase
      .channel("realtime-leads")
      .on("postgres_changes", { event: "*", schema: "public", table: "leads" }, (payload) => {
        if (payload.eventType === "INSERT") {
          setLeads(prev => [payload.new, ...prev]);
        } else if (payload.eventType === "UPDATE") {
          setLeads(prev => prev.map(l => l.id === payload.new.id ? payload.new : l));
        }
      })
      .subscribe();

    return () => {
        supabase.removeChannel(channel);
    };
  }, []);

  const getSourceIcon = (source) => {
    if (source?.includes("whatsapp")) return <MessageSquare className="w-4 h-4 text-emerald-600" />;
    if (source?.includes("telegram")) return <Phone className="w-4 h-4 text-sky-600" />;
    if (source?.includes("instagram")) return <Instagram className="w-4 h-4 text-pink-600" />;
    if (source?.includes("vapi")) return <Phone className="w-4 h-4 text-purple-600" />;
    return <User className="w-4 h-4" style={{ color: 'var(--graphics)' }} />;
  };

  return (
    <div className="flex flex-row md:grid md:grid-cols-4 gap-6 h-[calc(100vh-220px)] md:h-[calc(100vh-280px)] overflow-x-auto overflow-y-hidden snap-x snap-mandatory hide-scrollbar w-full pb-4">
      {Object.keys(STAGE_LABELS).map((stage) => (
        <div key={stage} className="flex flex-col gap-4 min-w-[85vw] md:min-w-0 snap-center snap-always transition-all duration-300">
          <div className="flex items-center justify-between px-2">
            <h3 className="text-sm font-semibold uppercase tracking-widest" style={{ color: 'var(--copy)' }}>
                {STAGE_LABELS[stage]} <span className="ml-2 px-2 py-0.5 rounded-full text-xs" style={{ color: 'var(--copy)', backgroundColor: 'var(--bg-input)' }}>
                    {leads.filter(l => l.stage === stage).length}
                </span>
            </h3>
          </div>

          <div className="flex-1 overflow-y-auto space-y-4 custom-scrollbar pr-2 pb-12 md:pb-8">
            {leads.filter(l => l.stage === stage).map((lead) => (
              <div
                key={lead.id}
                onClick={() => onSelectLead(lead)}
                className="glass-card transition-all cursor-pointer p-5 md:p-4 group active:scale-[0.98]"
                style={{ 
                  '--accent': 'var(--primary)',
                  '--accent-soft': 'var(--primary-alt)'
                }}
                onMouseOver={(e) => {
                  e.currentTarget.style.borderColor = 'var(--primary)';
                  e.currentTarget.style.boxShadow = 'var(--shadow-md)';
                }}
                onMouseOut={(e) => {
                  e.currentTarget.style.borderColor = 'var(--border)';
                  e.currentTarget.style.boxShadow = 'var(--shadow-sm)';
                }}
              >
                <div className="flex justify-between items-start mb-3">
                  <div className="flex items-center gap-2">
                    {getSourceIcon(lead.source)}
                    <span className="text-xs font-medium truncate w-24" style={{ color: 'var(--copy)' }}>
                        {lead.phone_number}
                    </span>
                  </div>
                  {lead.seriousness_score >= 8 && (
                    <Flame className="w-4 h-4 text-orange-500 animate-pulse" />
                  )}
                </div>

                <h4 className="font-semibold transition-colors" style={{ color: 'var(--heading)' }}>
                    {lead.name || "Unknown Prospect"}
                </h4>

                <div className="mt-3 flex items-center justify-between">
                  <div className="flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-bold" style={{ color: 'var(--copy)', backgroundColor: 'var(--bg-input)' }}>
                    SCORE: <span style={{ color: lead.seriousness_score >= 8 ? '#EA580C' : 'var(--heading)' }}>
                        {lead.seriousness_score || "?"}
                    </span>
                  </div>
                  <span className="text-[10px] font-medium" style={{ color: 'var(--graphics)' }}>
                    {lead.updated_at ? formatDistanceToNow(new Date(lead.updated_at)) : "just now"} ago
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
