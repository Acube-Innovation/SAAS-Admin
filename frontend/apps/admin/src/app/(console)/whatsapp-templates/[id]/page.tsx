import { Suspense } from "react";

import { WhatsAppEditor } from "@/components/whatsapp/wa-editor";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <WhatsAppEditor id={id} />
    </Suspense>
  );
}
