import type { Metadata } from "next";
import { MyTrips } from "@/components/account/MyTrips";

export const metadata: Metadata = {
  title: "My trips",
  description: "Your AngelCare Transit rides.",
};

export default function Page() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">My trips</h1>
      <p className="mt-4 text-foreground/70">Your requests and rides, newest first.</p>
      <div className="mt-10">
        <MyTrips />
      </div>
    </div>
  );
}
