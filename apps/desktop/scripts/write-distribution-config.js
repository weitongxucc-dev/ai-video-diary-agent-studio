const fs = require('fs');
const path = require('path');

const authMode = process.env.NEXT_PUBLIC_AUTH_MODE === 'supabase' ? 'supabase' : 'local';
const supabaseUrl = (process.env.NEXT_PUBLIC_SUPABASE_URL || '').trim();
const supabasePublishableKey = (
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || ''
).trim();

if (authMode === 'supabase' && (!supabaseUrl || !supabasePublishableKey)) {
  throw new Error(
    'Supabase distribution builds require NEXT_PUBLIC_SUPABASE_URL and ' +
    'NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY',
  );
}

const outputPath = path.join(
  __dirname,
  '..',
  'assets',
  'distribution-config.generated.json',
);
const packagedUrl = authMode === 'supabase' ? supabaseUrl : '';
const packagedPublishableKey = authMode === 'supabase' ? supabasePublishableKey : '';
fs.writeFileSync(
  outputPath,
  `${JSON.stringify({
    authMode,
    supabaseUrl: packagedUrl,
    supabasePublishableKey: packagedPublishableKey,
  }, null, 2)}\n`,
  { mode: 0o600 },
);
console.log(`Wrote ${authMode} distribution config`);
