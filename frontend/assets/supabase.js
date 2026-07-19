import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm";

const SUPABASE_URL = "https://tblljdxemwfvlpuouzjp.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_QtStXxsZLR511cBIOp7MLA_q-dIZZOl";

export const supabase = createClient(
  SUPABASE_URL,
  SUPABASE_PUBLISHABLE_KEY,
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
    },
  },
);
