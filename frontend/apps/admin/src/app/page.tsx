import { redirect } from "next/navigation";

import { HOME } from "@/lib/auth";

export default function Home() {
  redirect(HOME);
}
