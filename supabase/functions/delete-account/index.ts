import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const jsonResponse = (status: number, body: object) => new Response(
  JSON.stringify(body),
  { status, headers: { ...corsHeaders, "Content-Type": "application/json" } },
);

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (request.method !== "POST") return jsonResponse(405, { error: "Method not allowed" });

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !anonKey || !serviceRoleKey) {
    return jsonResponse(500, { error: "Account deletion is not configured" });
  }

  const authorization = request.headers.get("Authorization") || "";
  const token = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!token) return jsonResponse(401, { error: "Invalid session" });

  const authClient = createClient(supabaseUrl, anonKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: authData, error: authError } = await authClient.auth.getUser(token);
  const user = authData.user;
  if (authError || !user?.email) return jsonResponse(401, { error: "Invalid session" });

  let body: { email?: unknown };
  try {
    body = await request.json();
  } catch {
    return jsonResponse(400, { error: "Invalid request" });
  }
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!email || email !== user.email.trim().toLowerCase()) {
    return jsonResponse(400, { error: "Email confirmation does not match" });
  }

  const admin = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const label = user.id.slice(0, 8);

  try {
    for (const table of [
      "user_settings",
      "collection_requests",
      "chart_entries",
      "chart_periods",
      "profiles",
    ]) {
      const { error } = await admin.from(table).delete().eq("user_id", user.id);
      if (error) throw new Error(`${table}: ${error.message}`);
    }

    const { data: objects, error: listError } = await admin.storage
      .from("echorank-state")
      .list(user.id, { limit: 1000 });
    if (listError && !/not found/i.test(listError.message)) {
      throw new Error(`storage list: ${listError.message}`);
    }
    const objectPaths = (objects || []).map((object) => `${user.id}/${object.name}`);
    if (objectPaths.length) {
      const { error: removeError } = await admin.storage
        .from("echorank-state")
        .remove(objectPaths);
      if (removeError) throw new Error(`storage remove: ${removeError.message}`);
    }

    const { error: deleteError } = await admin.auth.admin.deleteUser(user.id);
    if (deleteError) throw new Error(`auth: ${deleteError.message}`);
  } catch (error) {
    console.error(`Account deletion failed for ${label}:`, error instanceof Error ? error.message : "unknown");
    return jsonResponse(500, { error: "Account deletion failed" });
  }

  return jsonResponse(200, { deleted: true });
});
