type Endpoint = "VITE_API_URL" | "VITE_WS_URL" | "VITE_STORAGE_URL";
export function deploymentURLs(mode: string, release?: boolean): Record<Endpoint, URL>;
export function securityPolicy(urls: Record<Endpoint, URL>, desktop?: boolean, devNonce?: string): string;
