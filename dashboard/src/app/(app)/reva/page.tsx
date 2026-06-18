"use client";

import { useState, useEffect } from "react";
import KanbanBoard from "@/components/KanbanBoard";
import KpiBar from "@/components/KpiBar";
import LeadDetailModal from "@/components/LeadDetailModal";
import PropertyManager from "@/components/PropertyManager";

export default function RevaDashboardPage() {
  const [activeTab, setActiveTab] = useState<"leads" | "properties">("leads");
  const [selectedLead, setSelectedLead] = useState<any>(null);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Neoscona Reva</h1>
        <p className="text-gray-600">AI sales engine for real estate</p>
      </div>

      {/* KPI Bar */}
      <div className="mb-8">
        <KpiBar />
      </div>

      {/* Tabs */}
      <div className="flex gap-4 mb-6 border-b border-gray-200">
        <button
          onClick={() => setActiveTab("leads")}
          className={`pb-4 px-2 border-b-2 font-medium text-sm transition-colors ${
            activeTab === "leads"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          Leads
        </button>
        <button
          onClick={() => setActiveTab("properties")}
          className={`pb-4 px-2 border-b-2 font-medium text-sm transition-colors ${
            activeTab === "properties"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
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
  );
}
