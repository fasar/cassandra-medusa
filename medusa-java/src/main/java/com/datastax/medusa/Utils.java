package com.datastax.medusa;

public class Utils {
    public static boolean evaluateBoolean(String value) {
        if (value == null) {
            return false;
        }
        return value.equalsIgnoreCase("true") || value.equalsIgnoreCase("yes") || value.equals("1");
    }
}
