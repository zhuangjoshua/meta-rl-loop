// Minimal RN UI primitives (analogs of the web scaffold's components/ui). Intentionally plain;
// the CEO worker restyles per brand before publish.
import React from "react";
import { Pressable, Text, View, TextInput, StyleSheet, ActivityIndicator } from "react-native";
import { theme } from "../lib/theme";

export function Card({ children }: { children: React.ReactNode }) {
  return <View style={s.card}>{children}</View>;
}
export function CardHeader({ children }: { children: React.ReactNode }) {
  return <View style={{ marginBottom: 8 }}>{children}</View>;
}
export function CardTitle({ children }: { children: React.ReactNode }) {
  return <Text style={s.title}>{children}</Text>;
}
export function CardDescription({ children }: { children: React.ReactNode }) {
  return <Text style={s.muted}>{children}</Text>;
}
export function CardContent({ children }: { children: React.ReactNode }) {
  return <View style={{ gap: 10 }}>{children}</View>;
}

export function Button({
  children,
  onPress,
  disabled,
  variant = "primary",
  busy,
}: {
  children: React.ReactNode;
  onPress?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "destructive";
  busy?: boolean;
}) {
  const bg =
    variant === "destructive" ? theme.danger : variant === "secondary" ? theme.card : theme.accent;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || busy}
      style={[s.btn, { backgroundColor: bg, opacity: disabled || busy ? 0.5 : 1 }]}
    >
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={s.btnText}>{children}</Text>}
    </Pressable>
  );
}

export function Input(props: React.ComponentProps<typeof TextInput>) {
  return <TextInput placeholderTextColor={theme.muted} style={s.input} {...props} />;
}

export function Screen({ children }: { children: React.ReactNode }) {
  return <View style={s.screen}>{children}</View>;
}

export { Text, View };

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.bg, padding: theme.space, gap: theme.space },
  card: { backgroundColor: theme.card, borderRadius: theme.radius, padding: theme.space, gap: 6 },
  title: { color: theme.text, fontSize: 18, fontWeight: "700" },
  muted: { color: theme.muted, fontSize: 14 },
  btn: { borderRadius: theme.radius, paddingVertical: 14, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "700", fontSize: 16 },
  input: {
    backgroundColor: "#1e1e2a",
    color: theme.text,
    borderRadius: theme.radius,
    padding: 12,
    fontSize: 16,
  },
});
