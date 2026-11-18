"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { signUp } from "../../lib/auth-client";
import { trackEvent } from "@/lib/analytics";
import { attributionMetadata, getAnonymousAttribution } from "@/lib/attribution";
import { postAuthPath } from "@/lib/auth-redirect";
import { track } from "@/lib/datafast";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function SignUp() {
  const t = useTranslations("auth");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setHydrated(true);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage("");
    setOk(false);

    const response = await signUp.email({
      email,
      password,
      name,
    });

    if (response.error) {
      setMessage(response.error.message || t("signUp.failed"));
      setLoading(false);
      return;
    }

    track("signup_completed", {
      auth_method: "email",
      ...attributionMetadata(getAnonymousAttribution()),
    });
    trackEvent("signup_completed", { method: "email" });
    setOk(true);
    setMessage(t("signUp.success"));
    setLoading(false);

    // Automatically sign in after successful sign up
    setTimeout(() => {
      const next = postAuthPath(window.location.search);
      const prefix = window.location.pathname.startsWith("/app/supoclip") ? "/app/supoclip" : "";
      const dest = next === "/" ? `${prefix}/list` : (prefix && !next.startsWith(prefix) ? `${prefix}${next}` : next);
      window.location.assign(dest);
    }, 400);
  };

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">{t("signUp.title")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("signUp.subtitle")}</p>
      <div className="mt-8">
        <form onSubmit={(e) => { if (!hydrated) { e.preventDefault(); return; } return handleSubmit(e); }} className="space-y-4">
          <Input
            className="h-11"
            type="text"
            placeholder={t("signUp.name")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            disabled={loading}
          />
          <Input
            className="h-11"
            type="email"
            placeholder={t("signUp.email")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={loading}
          />
          <Input
            className="h-11"
            type="password"
            placeholder={t("signUp.password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={loading}
            minLength={8}
          />
          <Button type="submit" className="h-11 w-full" disabled={!hydrated || loading}>
            {loading ? t("signUp.submitting") : t("signUp.submit")}
          </Button>
        </form>
        {message && (
          <p className={`mt-4 text-sm ${ok ? "text-green-600" : "text-red-600"}`}>
            {message}
          </p>
        )}
      </div>
    </div>
  );
}
