export const money = (n: number) => new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND" }).format(n);
export const date = (value: string) => new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(new Date(value));
export const initials = (s: string) => s.trim().split(/\s+/).slice(0, 2).map(x => x[0]).join("").toUpperCase();
export const isOverdue = (due: string) => Date.parse(due) < Date.now();
