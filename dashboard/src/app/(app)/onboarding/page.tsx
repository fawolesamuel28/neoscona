'use client';

import { useEffect, useState } from 'react';
import api from '@/lib/axios';
import { useRouter } from 'next/navigation';

type OnboardingStep = 'org' | 'billing' | 'channel' | 'inventory' | 'complete';

export default function OnboardingPage() {
  const [step, setStep] = useState<OnboardingStep>('org');
  const [formData, setFormData] = useState({
    orgName: '',
    orgSlug: '',
  });
  const [error, setError] = useState('');
  const router = useRouter();

  useEffect(() => {
    const check = async () => {
      try {
        const res = await api.get('/onboarding/current');
        if (res.data.is_complete) router.push('/dashboard');
      } catch {
        router.push('/login');
      }
    };
    check();
  }, []);

  const handleOrgSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post('/organizations', {
        name: formData.orgName,
        slug: formData.orgSlug,
      });
      await api.post('/onboarding/step', { step: 'billing' });
      setStep('billing');
    } catch (err: any) {
      setError(err.response?.data?.error || 'Failed to create organization');
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 py-12 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Progress bar */}
        <div className="flex items-center gap-4 mb-12">
          {(['org', 'billing', 'channel', 'inventory', 'complete'] as OnboardingStep[]).map((s, i) => (
            <div key={s} className="flex items-center">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center font-semibold ${
                ['org', 'billing', 'channel', 'inventory', 'complete'].indexOf(s) <=
                ['org', 'billing', 'channel', 'inventory', 'complete'].indexOf(step)
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 text-gray-500'
              }`}>
                {i + 1}
              </div>
              {i < 4 && (
                <div className={`h-1 w-20 ${
                  ['org', 'billing', 'channel', 'inventory', 'complete'].indexOf(s) <
                  ['org', 'billing', 'channel', 'inventory', 'complete'].indexOf(step)
                    ? 'bg-blue-600'
                    : 'bg-gray-200'
                }`} />
              )}
            </div>
          ))}
        </div>

        {/* Step content */}
        <div className="bg-white rounded-2xl shadow-xl p-8">
          {step === 'org' && (
            <div>
              <h1 className="text-3xl font-bold text-gray-800 mb-2">Welcome to Neoscona!</h1>
              <p className="text-gray-600 mb-6">Let's create your organization</p>

              {error && <div className="bg-red-50 text-red-700 p-3 rounded-lg mb-4">{error}</div>}

              <form onSubmit={handleOrgSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Organization Name</label>
                  <input
                    type="text"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                    value={formData.orgName}
                    onChange={(e) => setFormData({ ...formData, orgName: e.target.value })}
                    required
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Slug</label>
                  <input
                    type="text"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                    value={formData.orgSlug}
                    onChange={(e) => setFormData({ ...formData, orgSlug: e.target.value.toLowerCase().replace(/ /g, '-') })}
                    required
                  />
                </div>

                <button
                  type="submit"
                  className="w-full bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors font-semibold"
                >
                  Continue
                </button>
              </form>
            </div>
          )}

          {step === 'billing' && (
            <div>
              <h1 className="text-3xl font-bold text-gray-800 mb-2">Choose a Plan</h1>
              <p className="text-gray-600 mb-6">Pick the perfect plan for your needs</p>

              <div className="grid md:grid-cols-3 gap-6">
                {[
                  {
                    name: 'Starter',
                    price: '₦5,000',
                    features: ['1,000 messages/month', '1 channel', '1 agent'],
                  },
                  {
                    name: 'Growth',
                    price: '₦15,000',
                    features: ['5,000 messages/month', '3 channels', '5 agents'],
                  },
                  {
                    name: 'Enterprise',
                    price: '₦50,000',
                    features: ['Unlimited messages', '10 channels', '50 agents'],
                  },
                ].map((plan) => (
                  <div key={plan.name} className="border border-gray-200 rounded-xl p-6 hover:border-blue-500 transition-colors">
                    <h3 className="text-xl font-bold text-gray-800 mb-2">{plan.name}</h3>
                    <p className="text-3xl font-bold text-blue-600 mb-4">{plan.price}/month</p>
                    <ul className="space-y-2 mb-6">
                      {plan.features.map((feat) => (
                        <li key={feat} className="flex items-center text-gray-600">
                          <span className="text-green-500 mr-2">✓</span>
                          {feat}
                        </li>
                      ))}
                    </ul>
                    <button className="w-full bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors font-semibold">
                      Choose Plan
                    </button>
                  </div>
                ))}
              </div>

              <button
                onClick={() => setStep('channel')}
                className="mt-6 w-full bg-gray-200 text-gray-700 py-2 px-4 rounded-lg hover:bg-gray-300 transition-colors font-semibold"
              >
                Skip for now
              </button>
            </div>
          )}

          {step === 'channel' && (
            <div>
              <h1 className="text-3xl font-bold text-gray-800 mb-2">Connect Channels</h1>
              <p className="text-gray-600 mb-6">Connect the channels you want Neoscona to manage</p>

              <div className="grid md:grid-cols-2 gap-4">
                {['WhatsApp', 'Telegram', 'Instagram', 'Voice'].map((channel) => (
                  <div key={channel} className="border border-gray-200 rounded-xl p-6 flex items-center gap-4 hover:border-blue-500 transition-colors cursor-pointer">
                    <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center text-blue-600 text-2xl">
                      💬
                    </div>
                    <div>
                      <h3 className="text-lg font-semibold text-gray-800">{channel}</h3>
                      <p className="text-gray-500">Connect {channel}</p>
                    </div>
                  </div>
                ))}
              </div>

              <button
                onClick={() => setStep('inventory')}
                className="mt-6 w-full bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors font-semibold"
              >
                Continue
              </button>
            </div>
          )}

          {step === 'inventory' && (
            <div>
              <h1 className="text-3xl font-bold text-gray-800 mb-2">Add Inventory</h1>
              <p className="text-gray-600 mb-6">Add your products or services</p>

              <button
                onClick={async () => {
                  await api.post('/onboarding/step', { step: 'complete', is_complete: true });
                  router.push('/dashboard');
                }}
                className="w-full bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors font-semibold"
              >
                Complete Setup
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}