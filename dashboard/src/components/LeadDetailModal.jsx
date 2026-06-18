"use client";
import { X, MessageSquare, MapPin, DollarSign, Calendar, Zap } from "lucide-react";

export default function LeadDetailModal({ lead, onClose }) {
  if (!lead) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/30 backdrop-blur-sm">
      <div className="glass-card w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden" style={{ borderColor: 'var(--primary-alt)', boxShadow: 'var(--shadow-md)' }}>
        <div className="p-6 border-b flex justify-between items-center" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-input)' }}>
          <div>
            <h2 className="text-2xl font-semibold" style={{ color: 'var(--heading)' }}>{lead.name || "Prospect Details"}</h2>
            <p className="text-sm" style={{ color: 'var(--copy)' }}>Lead ID: {lead.id}</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-full transition-colors" style={{ backgroundColor: 'transparent' }} onMouseOver={(e) => {e.currentTarget.style.backgroundColor = 'var(--bg-hover)'}} onMouseOut={(e) => {e.currentTarget.style.backgroundColor = 'transparent'}}>
            <X className="w-6 h-6" style={{ color: 'var(--copy)' }} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-8 grid grid-cols-1 md:grid-cols-3 gap-8">
          {/* Left: Summary Cards */}
          <div className="space-y-6">
            <div className="space-y-4">
              <h3 className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--copy)' }}>Lead Signals</h3>
              <div className="grid grid-cols-1 gap-3">
                <div className="p-4 rounded-xl border flex items-center gap-4" style={{ backgroundColor: 'var(--bg-input)', borderColor: 'var(--border)' }}>
                  <div className="p-2 rounded-lg" style={{ backgroundColor: 'var(--primary-alt)' }}><Zap className="w-5 h-5" style={{ color: 'var(--primary)' }} /></div>
                  <div>
                    <p className="text-[10px] uppercase font-bold" style={{ color: 'var(--copy)' }}>Seriousness</p>
                    <p className="text-lg font-semibold" style={{ color: 'var(--heading)' }}>{lead.seriousness_score || "?"}/10</p>
                  </div>
                </div>
                <div className="p-4 rounded-xl border flex items-center gap-4" style={{ backgroundColor: 'var(--bg-input)', borderColor: 'var(--border)' }}>
                  <div className="bg-emerald-100 p-2 rounded-lg"><DollarSign className="w-5 h-5 text-emerald-600" /></div>
                  <div>
                    <p className="text-[10px] uppercase font-bold" style={{ color: 'var(--copy)' }}>Budget</p>
                    <p className="text-lg font-semibold" style={{ color: 'var(--heading)' }}>{lead.budget || "N/A"}</p>
                  </div>
                </div>
                <div className="p-4 rounded-xl border flex items-center gap-4" style={{ backgroundColor: 'var(--bg-input)', borderColor: 'var(--border)' }}>
                  <div className="bg-sky-100 p-2 rounded-lg"><MapPin className="w-5 h-5 text-sky-600" /></div>
                  <div>
                    <p className="text-[10px] uppercase font-bold" style={{ color: 'var(--copy)' }}>Location</p>
                    <p className="text-lg font-semibold" style={{ color: 'var(--heading)' }}>{lead.location || "Undisclosed"}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Right: Timeline / Activity Placeholder */}
          <div className="md:col-span-2 space-y-6">
             <div className="space-y-4 h-full flex flex-col">
                <h3 className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--copy)' }}>Conversation Intelligence</h3>
                <div className="flex-1 rounded-2xl border p-6 space-y-4 overflow-y-auto custom-scrollbar" style={{ backgroundColor: 'var(--bg-input)', borderColor: 'var(--border)' }}>
                    <p className="text-sm italic" style={{ color: 'var(--copy)' }}>Historical transcripts are synced here in real-time...</p>
                    {/* Message items would go here */}
                    {lead.conversation && (
                        <div className="flex flex-col gap-2 max-w-[80%] p-3 rounded-tr-xl rounded-br-xl rounded-bl-xl border-l-2" style={{ backgroundColor: 'var(--primary-alt)', borderLeftColor: 'var(--primary)' }}>
                            <p className="text-xs font-bold uppercase" style={{ color: 'var(--primary)' }}>Input</p>
                            <p className="text-sm" style={{ color: 'var(--heading)' }}>{lead.conversation}</p>
                        </div>
                    )}
                </div>
             </div>
          </div>
        </div>

        <div className="p-6 border-t flex justify-end gap-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-input)' }}>
            <button className="px-6 py-2 border rounded-lg text-sm font-semibold transition-colors" style={{ borderColor: 'var(--border)', color: 'var(--heading)' }} onMouseOver={(e) => {e.currentTarget.style.backgroundColor = 'var(--surface)'}} onMouseOut={(e) => {e.currentTarget.style.backgroundColor = 'transparent'}}>
                Archive Lead
            </button>
            <button className="px-6 py-2 text-white rounded-lg text-sm font-semibold transition-colors" style={{ backgroundColor: 'var(--primary)' }} onMouseOver={(e) => {e.currentTarget.style.backgroundColor = 'var(--primary-dark)'}} onMouseOut={(e) => {e.currentTarget.style.backgroundColor = 'var(--primary)'}}>
                Take Over Manually
            </button>
        </div>
      </div>
    </div>
  );
}
