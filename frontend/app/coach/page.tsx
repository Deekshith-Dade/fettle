import type { Metadata } from "next";
import CoachChat from "../../components/coach-chat";

export const metadata: Metadata = {
  title: "coach — fettle",
  description: "Ask your health data anything.",
};

export default function CoachPage() {
  return <CoachChat />;
}
