import { Suspense } from "react";

import { ClientDetail } from "@/components/client/client-detail";

export default async function ClientDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <ClientDetail id={id} />
    </Suspense>
  );
}
