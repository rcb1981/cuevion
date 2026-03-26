export default {
    content: ["./index.html", "./src/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                canvas: "#f4efe7",
                ink: "#1f2a24",
                moss: "#355648",
                pine: "#264238",
                sand: "#efe5d6",
                clay: "#b9855b",
            },
            boxShadow: {
                panel: "0 24px 80px rgba(39, 49, 44, 0.12)",
            },
            fontFamily: {
                sans: ["ui-sans-serif", "system-ui", "sans-serif"],
            },
        },
    },
    plugins: [],
};
