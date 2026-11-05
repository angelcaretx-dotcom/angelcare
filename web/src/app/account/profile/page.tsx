import type { Metadata } from "next";
import { ProfileEditor } from "@/components/account/ProfileEditor";

export const metadata: Metadata = {
  title: "Profile & addresses",
  description: "Your details and saved addresses for booking rides.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Profile &amp; addresses</h1>
      <p className="mt-4 text-foreground/70">Keep your details and favorite addresses here so booking takes seconds.</p>
      <div className="mt-10">
        <ProfileEditor />
      </div>
    </div>
  );
}
