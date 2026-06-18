"use client";
import { useEffect, useState } from "react";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import { Plus, Home, MapPin, Building2 } from "lucide-react";

export default function PropertyManager() {
  const supabase = createClientComponentClient();
  const [properties, setProperties] = useState([]);

  useEffect(() => {
    async function fetchInventory() {
      const { data } = await supabase.table("properties").select("*");
      if (data) setProperties(data);
    }
    fetchInventory();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h3 className="text-xl font-semibold" style={{ color: 'var(--heading)' }}>Active Inventory</h3>
          <p className="text-sm" style={{ color: 'var(--copy)' }}>Reva uses these details for accurate lead qualification.</p>
        </div>
        <button className="flex items-center gap-2 px-4 py-2 text-sm font-bold text-white rounded-lg transition-all" style={{ backgroundColor: 'var(--primary)' }} onMouseOver={(e) => {e.currentTarget.style.backgroundColor = 'var(--primary-dark)'}} onMouseOut={(e) => {e.currentTarget.style.backgroundColor = 'var(--primary)'}}>
          <Plus className="w-4 h-4" /> Add Property
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {properties.map((prop) => (
          <div key={prop.id} className="glass-card overflow-hidden group">
            <div className="h-32 relative flex items-center justify-center" style={{ backgroundColor: 'var(--bg-input)' }}>
                <Building2 className="w-12 h-12 group-hover:scale-110 transition-transform" style={{ color: 'var(--graphics)' }} />
                <div className="absolute top-2 right-2 text-white text-[10px] font-bold px-2 py-1 rounded" style={{ backgroundColor: 'var(--primary)' }}>
                    {prop.type || "Unit"}
                </div>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <h4 className="font-semibold text-lg" style={{ color: 'var(--heading)' }}>{prop.name}</h4>
                <div className="flex items-center gap-1 text-sm mt-1" style={{ color: 'var(--copy)' }}>
                  <MapPin className="w-3 h-3" /> {prop.location}
                </div>
              </div>
              <div className="flex justify-between items-end border-t pt-4" style={{ borderColor: 'var(--border)' }}>
                <div>
                  <p className="text-[10px] uppercase font-bold" style={{ color: 'var(--copy)' }}>List Price</p>
                  <p className="text-lg font-semibold" style={{ color: 'var(--primary)' }}>₦{prop.price?.toLocaleString()}</p>
                </div>
                <button className="text-xs hover:underline font-semibold" style={{ color: 'var(--primary)' }}>
                    Edit Details
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
