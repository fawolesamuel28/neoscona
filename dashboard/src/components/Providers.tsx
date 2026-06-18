'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs';

export default function Providers({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const supabase = createClientComponentClient();
  const [activeProduct, setActiveProduct] = useState('neoscona');
  const [user, setUser] = useState<any>(null);

  useEffect(() => {
    const checkAuth = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      setUser(session?.user ?? null);
    };
    checkAuth();

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setUser(session?.user ?? null);
      }
    );

    return () => subscription.unsubscribe();
  }, [supabase]);

  const isAuthPage = pathname?.startsWith('/login') || pathname?.startsWith('/signup');

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push('/login');
  };

  const handleProductChange = (value: string) => {
    setActiveProduct(value);
    switch (value) {
      case 'reva':
        router.push('/reva');
        break;
      case 'voice':
        // TODO: Route to voice product
        router.push('/');
        break;
      case 'reach':
        // TODO: Route to reach product
        router.push('/');
        break;
      default:
        router.push('/');
    }
  };

  return (
    <>
      {!isAuthPage && user && (
        <nav className="border-b border-gray-200 bg-white px-6 py-4">
          <div className="flex items-center justify-between max-w-7xl mx-auto">
            <div className="flex items-center gap-4">
              <Link href="/" className="flex items-center">
                <img src="/neoscona-logo.png" alt="Neoscona" style={{ height: 40, objectFit: 'contain' }} />
              </Link>
              
              {/* Product Switcher (Twilio-style) */}
              <div className="relative">
                <select
                  value={activeProduct}
                  onChange={(e) => handleProductChange(e.target.value)}
                  className="bg-gray-100 border border-gray-300 rounded-lg px-4 py-2 text-sm font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                >
                  <option value="neoscona">Neoscona Console</option>
                  <option value="reva">Neoscona Reva</option>
                  <option value="voice">Neoscona Voice</option>
                  <option value="reach">Neoscona Reach</option>
                </select>
              </div>
            </div>

            <div className="flex items-center gap-4">
              <Link href="/docs" className="text-sm font-medium text-gray-600 hover:text-gray-900">
                Docs
              </Link>
              <Link href="/settings" className="text-sm font-medium text-gray-600 hover:text-gray-900">
                Settings
              </Link>
              <button 
                onClick={handleSignOut}
                className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
              >
                Sign Out
              </button>
            </div>
          </div>
        </nav>
      )}

      {children}
    </>
  );
}
